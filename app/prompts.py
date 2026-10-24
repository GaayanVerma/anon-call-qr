"""Spoken prompts and reason codes shared by the Twilio and Exotel call flows."""

REASONS = {"1": "vehicle", "2": "lost_item", "3": "other"}
REASON_LABELS = {"vehicle": "a vehicle problem", "lost_item": "a lost item", "other": "another matter"}

MSG_GREETING = "Hello."
MSG_ENTER_CODE = "Enter the 6-digit code printed on the sticker, followed by the hash key."
MSG_NO_CODE = "We did not receive a code. Please check the sticker and call again. Goodbye."
MSG_INVALID_CODE = "This code is not valid. Please check the sticker and try again. Goodbye."
MSG_BLOCKED = "Sorry, we are unable to connect calls from this number. Goodbye."
MSG_CALLER_LIMIT = "You have made too many calls in a short time. Please try again later. Goodbye."
MSG_OWNER_LIMIT = "The owner has received too many calls recently. Please try again later. Goodbye."
MSG_REASON = (
    "Please tell us why you are calling. "
    "Press 1 for a vehicle problem. Press 2 for a lost item. Press 3 for anything else."
)
MSG_BAD_CHOICE = "Sorry, that is not a valid choice."
MSG_CONNECTING = "Connecting you to the owner. Your number will stay private."
MSG_NO_ANSWER = "The owner is not available right now. Please try again later. Goodbye."
MSG_FAILED = "Sorry, we could not connect your call. Please try again later. Goodbye."
MSG_ERROR = "Sorry, something went wrong. Please try again later. Goodbye."
