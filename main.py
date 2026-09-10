"""PNPRP Merit Bot.

This bot manages merit points, medal roles, approval requests, audit logs,
and generated service-record profile cards for a Discord roleplay server.
"""

from __future__ import annotations

import io
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont


TOKEN = os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not configured in Environment Variables.")

GUILD_ID = int(os.getenv("DISCORD_GUILD_ID", "1515094075215319221"))
MERIT_REQUEST_CHANNEL_ID = 1546798763606286396

# Adjust DB_PATH to stay relative to the container execution directory
DB_PATH = Path(os.getenv("MERIT_DB_PATH", "pnprp_merits.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

MEDALS = [
    {"name": "PNP COMMENDATION MEDAL", "required": 1000, "role_id": 1546812083822395473},
    {"name": "PNP EFFICIENCY MEDAL", "required": 2000, "role_id": 1546811994194321469},
    {"name": "PNP MEDAL OF MERIT", "required": 3000, "role_id": 1546811908479262741},
    {"name": "LUZON CAMPAIGN MEDAL", "required": 4000, "role_id": 1546812393064108054},
    {"name": "VISAYAS CAMPAIGN MEDAL", "required": 5000, "role_id": 1546830636982210660},
    {"name": "MINDANAO CAMPAIGN MEDAL", "required": 6000, "role_id": 1546830706699796550},
    {"name": "PNP GOOD CONDUCT MEDAL", "required": 7000, "role_id": 1546812295668178975},
    {"name": "POLICE REGULATION MEDAL", "required": 8000, "role_id": 1546812245642715218},
    {"name": "LAMBAT SIBAT", "required": 9000, "role_id": 1546812207847972934},
    {"name": "PNP UNIT CITATION", "required": 10000, "role_id": 1546812628750573588},
    {"name": "PRESIDENTIAL STREAMER", "required": 11000, "role_id": 1546812691581116497},
    {"name": "FEBRUARY REVOLUTION", "required": 12000, "role_id": 1546812734568529970},
    {"name": "EDSA II", "required": 13000, "role_id": 1546812776322965595},
    {"name": "PNP SANTO PAPA", "required": 14000, "role_id": 1546812808719499324},
    {"name": "PNP UNMIT", "required": 15000, "role_id": 1546812510345101425},
    {"name": "PNP KOSOVO", "required": 16000, "role_id": 1546812552506511460},
    {"name": "UNMIL LIBERIA", "required": 17000, "role_id": 1546812590053924864},
    {"name": "PNP OUTSTANDING CONDUCT MEDAL", "required": 18000, "role_id": 1546811812899594272},
    {"name": "PNP HEROISM MEDAL", "required": 19000, "role_id": 1546811746310946888},
    {"name": "PNP SERVICE MEDAL", "required": 20000, "role_id": 1546812352429695056},
    {"name": "PNP OUTSTANDING ACHIEVEMENT MEDAL", "required": 21000, "role_id": 1546811610251919390},
    {"name": "PNP SPECIAL SERVICE MEDAL", "required": 22000, "role_id": 1546811683497050173},
    {"name": "PNP DISTINGUISH SERVICE MEDAL", "required": 23000, "role_id": 1546811472141885450},
    {"name": "PNP WOUNDED PERSONNEL MEDAL", "required": 24000, "role_id": 1546812150746456114},
    {"name": "PNP MEDAL OF BRAVERY", "required": 25000, "role_id": 1546811536608329748},
    {"name": "PNP DISTINGUISH CONDUCT MEDAL", "required": 26000, "role_id": 1546811411617947648},
    {"name": "PNP MEDAL OF VALOR", "required": 27000, "role_id": 1546811267900113036},
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clip(value: str, limit: int = 1024) -> str:
    value = str(value)
    return value if len(value) <= limit else f"{value[: limit - 1]}…"


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


def is_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = attachment.content_type or ""
    return content_type.startswith("image/") or Path(attachment.filename).suffix.lower() in IMAGE_EXTENSIONS


db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db_lock = threading.RLock()


def initialize_database() -> None:
    with db_lock:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                merits INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                moderator_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                reason TEXT NOT NULL,
                proof TEXT,
                action TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS config (
                guild_id INTEGER PRIMARY KEY,
                request_channel_id INTEGER NOT NULL DEFAULT 0,
                logs_channel_id INTEGER NOT NULL DEFAULT 0,
                approver_role_id INTEGER NOT NULL DEFAULT 0,
                admin_channel_id INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS approver_roles (
                guild_id INTEGER NOT NULL,
                role_id INTEGER NOT NULL,
                PRIMARY KEY (guild_id, role_id)
            );

            CREATE TABLE IF NOT EXISTS requests (
                request_id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                user_ids TEXT NOT NULL,
                amount INTEGER NOT NULL DEFAULT 50,
                reason TEXT NOT NULL,
                proof TEXT NOT NULL,
                message_id INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'PENDING',
                approved_by INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )
        columns = {row["name"] for row in db.execute("PRAGMA table_info(config)")}
        if "admin_channel_id" not in columns:
            db.execute("ALTER TABLE config ADD COLUMN admin_channel_id INTEGER NOT NULL DEFAULT 0")
        for row in db.execute(
            "SELECT guild_id, approver_role_id FROM config WHERE approver_role_id != 0"
        ).fetchall():
            db.execute(
                "INSERT OR IGNORE INTO approver_roles (guild_id, role_id) VALUES (?, ?)",
                (row["guild_id"], row["approver_role_id"]),
            )
        db.commit()


def get_user_merits(user_id: int) -> int:
    with db_lock:
        row = db.execute("SELECT merits FROM users WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            db.execute("INSERT INTO users (user_id, merits) VALUES (?, 0)", (user_id,))
            db.commit()
            return 0
        return int(row["merits"])


def set_user_merits(user_id: int, amount: int) -> None:
    with db_lock:
        db.execute(
            """
            INSERT INTO users (user_id, merits) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET merits = excluded.merits
            """,
            (user_id, max(0, amount)),
        )
        db.commit()


def get_config(guild_id: int) -> tuple[int, int, int, int]:
    with db_lock:
        row = db.execute(
            """
            SELECT request_channel_id, logs_channel_id, approver_role_id, admin_channel_id
            FROM config WHERE guild_id = ?
            """,
            (guild_id,),
        ).fetchone()
        if row is None:
            db.execute("INSERT INTO config (guild_id) VALUES (?)", (guild_id,))
            db.commit()
            return (0, 0, 0, 0)
        return (
            int(row["request_channel_id"]),
            int(row["logs_channel_id"]),
            int(row["approver_role_id"]),
            int(row["admin_channel_id"]),
        )


def get_approver_role_ids(guild_id: int) -> set[int]:
    with db_lock:
        rows = db.execute(
            "SELECT role_id FROM approver_roles WHERE guild_id = ?",
            (guild_id,),
        ).fetchall()
    if rows:
        return {int(row["role_id"]) for row in rows}
    legacy_role_id = get_config(guild_id)[2]
    return {legacy_role_id} if legacy_role_id else set()


def set_approver_roles(guild_id: int, role_ids: Iterable[int]) -> None:
    unique_role_ids = list(dict.fromkeys(int(role_id) for role_id in role_ids))
    with db_lock:
        db.execute("DELETE FROM approver_roles WHERE guild_id = ?", (guild_id,))
        db.executemany(
            "INSERT INTO approver_roles (guild_id, role_id) VALUES (?, ?)",
            [(guild_id, role_id) for role_id in unique_role_ids],
        )
        db.commit()


def update_config(
    guild_id: int,
    request_channel_id: int | None = None,
    logs_channel_id: int | None = None,
    approver_role_id: int | None = None,
    admin_channel_id: int | None = None,
) -> None:
    old_request, old_logs, old_role, old_admin = get_config(guild_id)
    values = (
        old_request if request_channel_id is None else request_channel_id,
        old_logs if logs_channel_id is None else logs_channel_id,
        old_role if approver_role_id is None else approver_role_id,
        old_admin if admin_channel_id is None else admin_channel_id,
    )
    with db_lock:
        db.execute(
            """
            INSERT INTO config (
                guild_id, request_channel_id, logs_channel_id,
                approver_role_id, admin_channel_id
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                request_channel_id = excluded.request_channel_id,
                logs_channel_id = excluded.logs_channel_id,
                approver_role_id = excluded.approver_role_id,
                admin_channel_id = excluded.admin_channel_id
            """,
            (guild_id, *values),
        )
        db.commit()


def add_history(
    user_id: int,
    moderator_id: int,
    amount: int,
    reason: str,
    proof: str,
    action: str,
) -> None:
    with db_lock:
        db.execute(
            """
            INSERT INTO history (
                user_id, moderator_id, amount, reason, proof, action, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, moderator_id, amount, reason, proof, action, utc_now()),
        )
        db.commit()


def get_current_medal(merits: int) -> dict | None:
    current = None
    for medal in MEDALS:
        if merits >= medal["required"]:
            current = medal
    return current


def get_next_medal(merits: int) -> dict | None:
    return next((medal for medal in MEDALS if merits < medal["required"]), None)


def can_manage_merits(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        return False
    if interaction.user.guild_permissions.administrator:
        return True
    approver_role_ids = get_approver_role_ids(interaction.guild.id)
    return any(role.id in approver_role_ids for role in getattr(interaction.user, "roles", []))


async def require_admin_channel(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await interaction.response.send_message(
            "This command can only be used inside a server.", ephemeral=True
        )
        return False
    admin_channel_id = get_config(interaction.guild.id)[3]
    if admin_channel_id and interaction.channel_id != admin_channel_id:
        channel = interaction.guild.get_channel(admin_channel_id)
        location = channel.mention if channel else f"channel ID `{admin_channel_id}`"
        await interaction.response.send_message(
            f"Admin commands can only be used in {location}.", ephemeral=True
        )
        return False
    return True


async def find_member(guild: discord.Guild, user_id: int) -> discord.Member | None:
    member = guild.get_member(user_id)
    if member:
        return member
    try:
        return await guild.fetch_member(user_id)
    except (discord.NotFound, discord.Forbidden, discord.HTTPException):
        return None


async def update_medal_role(member: discord.Member) -> None:
    current_medal = get_current_medal(get_user_merits(member.id))
    for medal in MEDALS:
        role = member.guild.get_role(medal["role_id"])
        is_current_role = current_medal is not None and medal["role_id"] == current_medal["role_id"]
        if role and role in member.roles and not is_current_role:
            try:
                await member.remove_roles(role, reason="PNPRP merit medal update")
            except discord.Forbidden:
                pass
    if current_medal:
        role = member.guild.get_role(current_medal["role_id"])
        if role and role not in member.roles:
            try:
                await member.add_roles(role, reason="PNPRP merit medal update")
            except discord.Forbidden:
                pass


async def send_log(
    guild: discord.Guild,
    member: discord.Member,
    moderator: discord.abc.User,
    amount: int,
    reason: str,
    proof: str,
    action: str,
    old_total: int,
    new_total: int,
) -> None:
    logs_channel_id = get_config(guild.id)[1]
    channel = guild.get_channel(logs_channel_id) if logs_channel_id else None
    if not isinstance(channel, discord.TextChannel):
        return

    is_add = action == "ADD"
    embed = discord.Embed(
        title="MERIT AWARDED" if is_add else "MERIT REMOVED",
        color=discord.Color.green() if is_add else discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Personnel", value=f"{member.mention}\n`{member.id}`", inline=True)
    embed.add_field(name="Action by", value=moderator.mention, inline=True)
    embed.add_field(name="Merits", value=f"`{amount}`", inline=True)
    embed.add_field(name="Previous Total", value=f"`{old_total}`", inline=True)
    embed.add_field(name="New Total", value=f"`{new_total}`", inline=True)
    embed.add_field(name="Reason", value=clip(reason), inline=False)
    if proof:
        embed.add_field(name="Proof", value=f"[Open proof image]({proof})", inline=False)
        embed.set_image(url=proof)
    current_medal = get_current_medal(new_total)
    embed.add_field(
        name="Current Medal",
        value=current_medal["name"] if current_medal else "No medal yet",
        inline=False,
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="PNPRP Merit System")
    await channel.send(embed=embed)


def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Robust font loader for Docker/WispByte instances with fallback defaults."""
    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    candidates = (
        Path(".") / filename,
        Path("/usr/share/fonts/truetype/dejavu") / filename,
        Path("/usr/share/fonts/dejavu") / filename,
    )
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                pass
    return ImageFont.load_default()


async def create_profile_card(
    member: discord.Member,
    merits: int,
    current_medal: dict | None,
    next_medal: dict | None,
    history: Iterable[sqlite3.Row],
) -> io.BytesIO:
    width, height = 900, 980
    image = Image.new("RGB", (width, height), "#0d1b2a")
    draw = ImageDraw.Draw(image)

    for y in range(height):
        ratio = y / height
        color = (
            int(13 + 45 * ratio),
            int(27 + 24 * ratio),
            int(42 + 70 * ratio),
        )
        draw.line((0, y, width, y), fill=color)

    font_header = load_font(28, bold=True)
    font_title = load_font(30, bold=True)
    font_text = load_font(22)
    font_small = load_font(18)

    draw.text((40, 36), "PHILIPPINE NATIONAL POLICE", fill="#f8fafc", font=font_header)
    draw.text((40, 78), "ROLEPLAY SERVICE RECORD", fill="#90cdf4", font=font_small)
    draw.text((40, 155), clip(member.display_name, 32), fill="#ffffff", font=font_title)
    draw.text((40, 205), f"{merits:,} MERITS", fill="#fbd38d", font=font_header)

    try:
        avatar_bytes = await member.display_avatar.read()
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGB").resize((190, 190))
        image.paste(avatar, (660, 34))
    except (discord.HTTPException, OSError, ValueError):
        pass

    draw.text((40, 290), "CURRENT MEDAL", fill="#90cdf4", font=font_small)
    draw.text(
        (40, 320),
        current_medal["name"] if current_medal else "NO MEDAL YET",
        fill="#ffffff",
        font=font_text,
    )
    draw.text((40, 390), "NEXT MEDAL", fill="#90cdf4", font=font_small)
    draw.text(
        (40, 420),
        next_medal["name"] if next_medal else "MAXIMUM MEDAL ACHIEVED",
        fill="#ffffff",
        font=font_text,
    )

    bar_x, bar_y, bar_w, bar_h = 40, 505, 820, 30
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h),
        radius=15,
        fill="#1e3a5f",
    )
    if next_medal:
        previous_required = current_medal["required"] if current_medal else 0
        range_required = next_medal["required"] - previous_required
        percentage = min(1.0, max(0, merits - previous_required) / range_required)
    else:
        percentage = 1.0
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + max(30, int(bar_w * percentage)), bar_y + bar_h),
        radius=15,
        fill="#63b3ed",
    )
    draw.text((40, 555), f"{int(percentage * 100)}% toward next medal", fill="#dbeafe", font=font_small)

    draw.text((40, 635), "RECENT SERVICE HISTORY", fill="#90cdf4", font=font_small)
    y_offset = 675
    recent_history = list(history)
    if not recent_history:
        draw.text((40, y_offset), "No history records found.", fill="#ffffff", font=font_text)
    else:
        for row in recent_history:
            prefix = "-" if row["action"] == "REMOVE" else "+"
            line = f"{prefix}{row['amount']}  {clip(row['reason'], 58)}"
            draw.text((40, y_offset), line, fill="#ffffff", font=font_small)
            y_offset += 40

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


class MeritRequestView(discord.ui.View):
    def __init__(self, request_id: int):
        super().__init__(timeout=None)
        self.request_id = request_id

    async def _authorize(self, interaction: discord.Interaction, action: str) -> bool:
        if not can_manage_merits(interaction):
            await interaction.response.send_message(
                f"You are not authorized to {action} merit requests.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label="Approve",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="pnpr_merit_approve",
    )
    async def approve(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._authorize(interaction, "approve"):
            return
        if interaction.guild is None:
            return

        with db_lock:
            request = db.execute(
                """
                SELECT guild_id, user_ids, amount, reason, proof, status
                FROM requests WHERE request_id = ?
                """,
                (self.request_id,),
            ).fetchone()
        if request is None:
            await interaction.response.send_message("This request no longer exists.", ephemeral=True)
            return
        if request["guild_id"] != interaction.guild.id:
            await interaction.response.send_message("This request belongs to another server.", ephemeral=True)
            return
        if request["status"] != "PENDING":
            await interaction.response.send_message(
                f"This request is already {str(request['status']).lower()}.", ephemeral=True
            )
            return

        await interaction.response.defer()
        processed: list[str] = []
        for raw_id in str(request["user_ids"]).split(","):
            if not raw_id:
                continue
            member = await find_member(interaction.guild, int(raw_id))
            if member is None:
                continue
            old_total = get_user_merits(member.id)
            new_total = old_total + int(request["amount"])
            set_user_merits(member.id, new_total)
            add_history(
                member.id,
                interaction.user.id,
                int(request["amount"]),
                request["reason"],
                request["proof"],
                "APPROVED",
            )
            await update_medal_role(member)
            await send_log(
                interaction.guild,
                member,
                interaction.user,
                int(request["amount"]),
                request["reason"],
                request["proof"],
                "ADD",
                old_total,
                new_total,
            )
            processed.append(member.mention)

        with db_lock:
            db.execute(
                "UPDATE requests SET status = 'APPROVED', approved_by = ? WHERE request_id = ?",
                (interaction.user.id, self.request_id),
            )
            db.commit()

        embed = discord.Embed(
            title="MERIT REQUEST APPROVED",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name=f"Members Awarded (+{request['amount']} Merits Each)",
            value=", ".join(processed) if processed else "No current members found",
            inline=False,
        )
        embed.add_field(name="Reason", value=clip(request["reason"]), inline=False)
        embed.add_field(name="Proof", value=f"[Open proof image]({request['proof']})", inline=False)
        embed.add_field(name="Approved By", value=interaction.user.mention, inline=True)
        embed.set_image(url=request["proof"])
        embed.set_footer(text="PNPRP Merit System")
        for child in self.children:
            child.disabled = True
        if interaction.message:
            await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(
        label="Disapprove",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="pnpr_merit_disapprove",
    )
    async def disapprove(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not await self._authorize(interaction, "disapprove"):
            return
        with db_lock:
            request = db.execute(
                "SELECT guild_id, reason, status FROM requests WHERE request_id = ?",
                (self.request_id,),
            ).fetchone()
        if request is None:
            await interaction.response.send_message("This request no longer exists.", ephemeral=True)
            return
        if interaction.guild is None or request["guild_id"] != interaction.guild.id:
            await interaction.response.send_message("This request belongs to another server.", ephemeral=True)
            return
        if request["status"] != "PENDING":
            await interaction.response.send_message(
                f"This request is already {str(request['status']).lower()}.", ephemeral=True
            )
            return

        with db_lock:
            db.execute(
                "UPDATE requests SET status = 'DISAPPROVED', approved_by = ? WHERE request_id = ?",
                (interaction.user.id, self.request_id),
            )
            db.commit()

        embed = discord.Embed(
            title="MERIT REQUEST DISAPPROVED",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="Reason", value=clip(request["reason"]), inline=False)
        embed.add_field(name="Disapproved By", value=interaction.user.mention, inline=True)
        embed.set_footer(text="PNPRP Merit System")
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)


intents = discord.Intents.default()
intents.members = True
bot = commands.Bot(command_prefix=commands.when_mentioned, intents=intents)
initialize_database()
synced_once = False
PROCESS_STARTED_AT = datetime.now(timezone.utc)


async def restore_pending_views() -> None:
    with db_lock:
        rows = db.execute(
            "SELECT request_id, message_id FROM requests WHERE status = 'PENDING' AND message_id != 0"
        ).fetchall()
    for row in rows:
        bot.add_view(MeritRequestView(int(row["request_id"])), message_id=int(row["message_id"]))


@bot.event
async def on_ready() -> None:
    global synced_once
    print(f"PNPRP Merit Bot online as {bot.user}")
    global_synced = await bot.tree.sync()
    if not synced_once:
        await restore_pending_views()
        synced_once = True
    command_names = ", ".join(f"/{command.name}" for command in global_synced)
    print(
        f"Removed old server-only commands and synced {len(global_synced)} "
        f"global commands ({command_names})."
    )


@bot.tree.command(name="botstatus", description="Check whether the PNPRP Merit Bot is online.")
async def botstatus(interaction: discord.Interaction) -> None:
    uptime = datetime.now(timezone.utc) - PROCESS_STARTED_AT
    total_seconds = max(0, int(uptime.total_seconds()))
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_text = (
        f"{days}d {hours}h {minutes}m {seconds}s"
        if days
        else f"{hours}h {minutes}m {seconds}s"
    )
    await interaction.response.send_message(
        f"PNPRP Merit Bot is online as **{bot.user}**.\nUptime: `{uptime_text}`",
        ephemeral=True,
    )


@bot.tree.command(name="setup", description="Configure the PNPRP Merit System.")
@app_commands.describe(
    request_channel="Channel where merit requests will appear",
    logs_channel="Channel for merit logs",
    approver_role="First role allowed to approve merit requests",
    approver_role2="Additional approver role (optional)",
    approver_role3="Additional approver role (optional)",
    approver_role4="Additional approver role (optional)",
    approver_role5="Additional approver role (optional)",
    approver_role6="Additional approver role (optional)",
    approver_role7="Additional approver role (optional)",
    approver_role8="Additional approver role (optional)",
    approver_role9="Additional approver role (optional)",
    approver_role10="Additional approver role (optional)",
)
async def setup(
    interaction: discord.Interaction,
    request_channel: discord.TextChannel,
    logs_channel: discord.TextChannel,
    approver_role: discord.Role,
    approver_role2: discord.Role | None = None,
    approver_role3: discord.Role | None = None,
    approver_role4: discord.Role | None = None,
    approver_role5: discord.Role | None = None,
    approver_role6: discord.Role | None = None,
    approver_role7: discord.Role | None = None,
    approver_role8: discord.Role | None = None,
    approver_role9: discord.Role | None = None,
    approver_role10: discord.Role | None = None,
) -> None:
    if interaction.guild is None or not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Only server administrators can configure the merit system.", ephemeral=True
        )
        return
    if not await require_admin_channel(interaction):
        return
    approver_roles = [
        role
        for role in (
            approver_role,
            approver_role2,
            approver_role3,
            approver_role4,
            approver_role5,
            approver_role6,
            approver_role7,
            approver_role8,
            approver_role9,
            approver_role10,
        )
        if role is not None
    ]
    update_config(
        interaction.guild.id,
        request_channel.id,
        logs_channel.id,
        approver_roles[0].id,
        interaction.channel_id,
    )
    set_approver_roles(interaction.guild.id, [role.id for role in approver_roles])
    embed = discord.Embed(title="PNPRP Merit System Configured", color=discord.Color.blurple())
    embed.add_field(name="Request Channel", value=request_channel.mention, inline=False)
    embed.add_field(name="Logs Channel", value=logs_channel.mention, inline=False)
    embed.add_field(
        name=f"Approver Roles ({len(approver_roles)})",
        value="\n".join(role.mention for role in approver_roles),
        inline=False,
    )
    embed.add_field(name="Designated Admin Channel", value=interaction.channel.mention, inline=False)
    embed.set_footer(text="PNPRP Merit System")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="addmerit", description="Directly award merits to a member.")
@app_commands.describe(user="Member receiving merits", howmany="Number of merits", reason="Reason")
async def addmerit(
    interaction: discord.Interaction,
    user: discord.Member,
    howmany: int,
    reason: str,
) -> None:
    if not await require_admin_channel(interaction):
        return
    if not can_manage_merits(interaction):
        await interaction.response.send_message("Permission denied.", ephemeral=True)
        return
    if howmany <= 0:
        await interaction.response.send_message("Amount must be greater than zero.", ephemeral=True)
        return

    old_total = get_user_merits(user.id)
    new_total = old_total + howmany
    set_user_merits(user.id, new_total)
    add_history(user.id, interaction.user.id, howmany, reason, "", "ADD")
    await update_medal_role(user)
    await send_log(
        interaction.guild,
        user,
        interaction.user,
        howmany,
        reason,
        "",
        "ADD",
        old_total,
        new_total,
    )
    current_medal = get_current_medal(new_total)
    embed = discord.Embed(
        title="MERITS ADDED",
        description=f"{user.mention} received **+{howmany} merits**.",
        color=discord.Color.green(),
    )
    embed.add_field(name="Previous", value=str(old_total), inline=True)
    embed.add_field(name="Added", value=f"+{howmany}", inline=True)
    embed.add_field(name="New Total", value=str(new_total), inline=True)
    embed.add_field(name="Reason", value=clip(reason), inline=False)
    embed.add_field(
        name="Current Medal",
        value=current_medal["name"] if current_medal else "No medal yet",
        inline=False,
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="removemerit", description="Deduct merits from a member.")
@app_commands.describe(user="Member losing merits", howmany="Number of merits to remove", reason="Reason")
async def removemerit(
    interaction: discord.Interaction,
    user: discord.Member,
    howmany: int,
    reason: str,
) -> None:
    if not await require_admin_channel(interaction):
        return
    if not can_manage_merits(interaction):
        await interaction.response.send_message("Permission denied.", ephemeral=True)
        return
    if howmany <= 0:
        await interaction.response.send_message("Amount must be greater than zero.", ephemeral=True)
        return

    old_total = get_user_merits(user.id)
    new_total = max(0, old_total - howmany)
    removed_amount = old_total - new_total
    set_user_merits(user.id, new_total)
    add_history(user.id, interaction.user.id, removed_amount, reason, "", "REMOVE")
    await update_medal_role(user)
    await send_log(
        interaction.guild,
        user,
        interaction.user,
        removed_amount,
        reason,
        "",
        "REMOVE",
        old_total,
        new_total,
    )
    current_medal = get_current_medal(new_total)
    embed = discord.Embed(
        title="MERITS REMOVED",
        description=f"{user.mention} lost **-{removed_amount} merits**.",
        color=discord.Color.red(),
    )
    embed.add_field(name="Previous", value=str(old_total), inline=True)
    embed.add_field(name="Deducted", value=f"-{removed_amount}", inline=True)
    embed.add_field(name="New Total", value=str(new_total), inline=True)
    embed.add_field(name="Reason", value=clip(reason), inline=False)
    embed.add_field(
        name="Current Medal",
        value=current_medal["name"] if current_medal else "No medal yet",
        inline=False,
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="merit_request", description="Request 50 merits for one or more personnel.")
@app_commands.describe(
    reason="Reason for the merit request",
    proof="Upload an image as proof",
    user1="Target member",
    user2="Target member (optional)",
    user3="Target member (optional)",
    user4="Target member (optional)",
    user5="Target member (optional)",
    user6="Target member (optional)",
    user7="Target member (optional)",
    user8="Target member (optional)",
    user9="Target member (optional)",
    user10="Target member (optional)",
)
async def merit_request(
    interaction: discord.Interaction,
    reason: str,
    proof: discord.Attachment,
    user1: discord.Member,
    user2: discord.Member | None = None,
    user3: discord.Member | None = None,
    user4: discord.Member | None = None,
    user5: discord.Member | None = None,
    user6: discord.Member | None = None,
    user7: discord.Member | None = None,
    user8: discord.Member | None = None,
    user9: discord.Member | None = None,
    user10: discord.Member | None = None,
) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return
    if interaction.channel_id != MERIT_REQUEST_CHANNEL_ID:
        await interaction.response.send_message(
            f"`/merit_request` can only be used in <#{MERIT_REQUEST_CHANNEL_ID}>.",
            ephemeral=True,
        )
        return
    if not is_image_attachment(proof):
        await interaction.response.send_message(
            "Proof must be an uploaded image file (PNG, JPG, GIF, WEBP, or BMP).",
            ephemeral=True,
        )
        return
    request_channel_id = get_config(interaction.guild.id)[0]
    channel = interaction.guild.get_channel(request_channel_id) if request_channel_id else None
    if not isinstance(channel, discord.TextChannel):
        await interaction.response.send_message(
            "The system is not set up. An administrator must use `/setup` first.",
            ephemeral=True,
        )
        return
    bot_member = interaction.guild.me
    if bot_member is not None:
        channel_permissions = channel.permissions_for(bot_member)
        missing_permissions = []
        if not channel_permissions.send_messages:
            missing_permissions.append("Send Messages")
        if not channel_permissions.embed_links:
            missing_permissions.append("Embed Links")
        if missing_permissions:
            await interaction.response.send_message(
                f"I cannot post merit requests in {channel.mention}. "
                f"Grant the bot: {', '.join(missing_permissions)}.",
                ephemeral=True,
            )
            return

    await interaction.response.defer(ephemeral=True)

    raw_users = [user1, user2, user3, user4, user5, user6, user7, user8, user9, user10]
    targets: list[discord.Member] = []
    for member in raw_users:
        if member and member not in targets:
            targets.append(member)

    user_ids = ",".join(str(member.id) for member in targets)
    proof_url = proof.url
    with db_lock:
        cursor = db.execute(
            """
            INSERT INTO requests (guild_id, user_ids, amount, reason, proof, created_at)
            VALUES (?, ?, 50, ?, ?, ?)
            """,
            (interaction.guild.id, user_ids, reason, proof_url, utc_now()),
        )
        request_id = int(cursor.lastrowid)
        db.commit()

    target_mentions = "\n".join(f"• {member.mention} (`{member.id}`)" for member in targets)
    embed = discord.Embed(
        title="MERIT REQUEST",
        color=discord.Color.gold(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Requested For (+50 Merits Each)", value=target_mentions, inline=False)
    embed.add_field(name="Reason", value=clip(reason), inline=False)
    embed.add_field(name="Proof Image", value=f"[Open proof image]({proof_url})", inline=False)
    embed.set_image(url=proof_url)
    embed.add_field(name="Requested By", value=interaction.user.mention, inline=False)
    embed.set_footer(text="PNPRP Merit System")

    try:
        message = await channel.send(embed=embed, view=MeritRequestView(request_id))
    except discord.Forbidden:
        with db_lock:
            db.execute("DELETE FROM requests WHERE request_id = ?", (request_id,))
            db.commit()
        await interaction.followup.send(
            f"I cannot post the merit request in {channel.mention}. "
            "Please check the bot's Send Messages and Embed Links permissions.",
            ephemeral=True,
        )
        return
    except discord.HTTPException:
        with db_lock:
            db.execute("DELETE FROM requests WHERE request_id = ?", (request_id,))
            db.commit()
        await interaction.followup.send(
            "Discord could not post the merit request. Please try again.",
            ephemeral=True,
        )
        return
    with db_lock:
        db.execute("UPDATE requests SET message_id = ? WHERE request_id = ?", (message.id, request_id))
        db.commit()
    await interaction.followup.send(
        f"Merit request sent to {channel.mention}.",
        ephemeral=True,
    )


@bot.tree.command(name="profile", description="View a member's PNPR service record card.")
@app_commands.describe(user="Member whose profile you want to view")
async def profile(interaction: discord.Interaction, user: discord.Member | None = None) -> None:
    await interaction.response.defer()
    user = user or interaction.user
    merits = get_user_merits(user.id)
    current_medal = get_current_medal(merits)
    next_medal = get_next_medal(merits)
    with db_lock:
        history = db.execute(
            """
            SELECT amount, reason, action
            FROM history WHERE user_id = ? ORDER BY id DESC LIMIT 5
            """,
            (user.id,),
        ).fetchall()
    image_buffer = await create_profile_card(user, merits, current_medal, next_medal, history)
    await interaction.followup.send(
        file=discord.File(fp=image_buffer, filename="profile.png")
    )


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction, error: app_commands.AppCommandError
) -> None:
    print(f"Command error: {error}")
    message = "Something went wrong while processing that command."
    if isinstance(error, app_commands.CommandSignatureMismatch):
        message = (
            "Discord was using an outdated command definition. "
            "Please close and reopen Discord, then try `/setup` again."
        )
    if isinstance(error, app_commands.CommandInvokeError) and isinstance(
        error.original, (discord.Forbidden, discord.NotFound)
    ):
        message = "Discord denied that action. Check the bot's permissions and role position."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except (discord.NotFound, discord.HTTPException):
        pass


bot.run(TOKEN)
