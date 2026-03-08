"""
Tool definitions passed to Claude for the prompt-first agent.

These are plain Anthropic API tool dicts — no framework.
"""

TOOLS: list[dict] = [
    {
        "name": "send_cleaner_email",
        "description": (
            "Send a query email to the cleaner asking whether the early check-in or "
            "late checkout is possible. Use this as the first action after receiving "
            "a guest request. Include the date and a clear, concise message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {
                    "type": "string",
                    "description": (
                        "The relevant date for the request (arrival date for early "
                        "check-in, departure date for late checkout). ISO format preferred."
                    ),
                },
                "message": {
                    "type": "string",
                    "description": (
                        "The message to send to the cleaner. Be concise: mention the "
                        "guest name, property, request type, and what flexibility is needed."
                    ),
                },
            },
            "required": ["date", "message"],
        },
    },
    {
        "name": "create_guest_draft",
        "description": (
            "Save a draft reply to the guest for the owner to review before sending. "
            "Use this once you have the cleaner's response and can give the guest a "
            "definitive answer. The owner will approve or edit the draft."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "body": {
                    "type": "string",
                    "description": (
                        "The message body to send to the guest. Should be warm, clear, "
                        "and directly answer whether their request is possible."
                    ),
                },
            },
            "required": ["body"],
        },
    },
    {
        "name": "wait",
        "description": (
            "Take no action right now. Use this when you have already sent the cleaner "
            "email and are waiting for their reply, or when the event does not require "
            "any action."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": "Brief explanation of why we are waiting.",
                },
            },
            "required": [],
        },
    },
]
