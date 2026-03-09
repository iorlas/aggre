"""Click CLI with commands: telegram-auth."""

from __future__ import annotations

import click

from aggre.config import load_config
from aggre.utils.db import get_engine


@click.group()
@click.option("--config", "config_path", default="config.yaml", help="Path to config YAML file.")
@click.pass_context
def cli(ctx: click.Context, config_path: str) -> None:
    """Aggre — Content aggregation system."""
    ctx.ensure_object(dict)
    cfg = load_config(config_path)
    ctx.obj["config"] = cfg

    engine = get_engine(cfg.settings.database_url)
    ctx.obj["engine"] = engine


@cli.command("telegram-auth")
@click.pass_context
def telegram_auth(ctx: click.Context) -> None:
    """Generate a Telegram session string for AGGRE_TELEGRAM_SESSION."""
    import asyncio

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    cfg = ctx.obj["config"]
    api_id = cfg.settings.telegram_api_id
    api_hash = cfg.settings.telegram_api_hash

    if not api_id or not api_hash:
        click.echo("Set AGGRE_TELEGRAM_API_ID and AGGRE_TELEGRAM_API_HASH first.")
        raise SystemExit(1)

    async def _auth() -> str:
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.start()
        session_str = client.session.save()
        await client.disconnect()
        return session_str

    session = asyncio.run(_auth())
    click.echo(f"\nAdd this to your .env file:\nAGGRE_TELEGRAM_SESSION={session}")
