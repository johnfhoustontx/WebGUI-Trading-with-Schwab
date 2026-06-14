"""config_notifications.example.py — template for Sentiment Dashboard notifications.

To enable Discord and/or Telegram posts from the dashboard:

  1. Copy this file to ``config_notifications.py`` in the same directory.
  2. Fill in the values below.
  3. Restart the dashboard. Startup log should say
     ``Notifier remote channels active: discord, telegram``.

The real file (``config_notifications.py``) is gitignored.

If the OptionsScanner repo at
``options-scanner/config_notifications.py`` (resolved via
``repo_paths.OPTIONS_SCANNER``) already has credentials, the dashboard will pick those up automatically
— no local copy needed. Override ``SentimentNotifier.SHARED_CONFIG_PATH``
in ``notifier.py`` if your OptionsScanner lives elsewhere.

Environment variables of the same name override anything set here.
"""

# Telegram — get the bot token from @BotFather. Get the chat ID by
# messaging your bot then visiting
# https://api.telegram.org/bot<TOKEN>/getUpdates and reading
# ``result[0].message.chat.id``.
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = 0

# Discord — create a webhook in
# Server > Channel > Edit Channel > Integrations > Webhooks > New Webhook.
DISCORD_WEBHOOK_URL = ""
