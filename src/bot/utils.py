from aiogram.types import CallbackQuery, Message


def callback_message(callback: CallbackQuery) -> Message | None:
    """`callback.message` is typed as `Message | InaccessibleMessage | None` — a callback can
    arrive for a message Telegram no longer lets us edit/reply to (e.g. it was deleted between
    the tap and our handler running). Narrow it once here instead of at every call site."""
    return callback.message if isinstance(callback.message, Message) else None
