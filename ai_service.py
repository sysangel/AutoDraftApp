"""
AI service: generates a professional email reply draft using OpenAI.

This module only DRAFTS a reply. It does not send anything.
"""

import os
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

# Prompt version string stored with each draft for auditing/iteration
PROMPT_VERSION = "v1"


def get_openai_client() -> OpenAI:
    """Create an OpenAI client using the OPENAI_API_KEY env variable."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY environment variable is not set.")
    return OpenAI(api_key=api_key)


def generate_draft_reply(
    sender: str,
    subject: str,
    cleaned_body: str,
) -> str:
    """
    Generate a plain-text email reply draft using OpenAI.

    Args:
        sender:       The From address of the inbound email.
        subject:      The subject line of the inbound email.
        cleaned_body: The cleaned body text of the inbound email.

    Returns:
        A plain-text draft reply body (no subject, no markdown).

    Raises:
        Exception on API or parsing failure.
    """
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    client = get_openai_client()

    system_prompt = (
        "You are a professional email assistant. "
        "Your job is to draft a helpful, concise, and polite reply to an inbound email. "
        "\n\nRules:"
        "\n- Output ONLY the plain text body of the reply email."
        "\n- Do NOT include a subject line."
        "\n- Do NOT use markdown, bullet points, or any formatting symbols."
        "\n- Do NOT make up facts, promises, or claims not supported by the inbound email."
        "\n- If important information is missing to give a full answer, politely ask for clarification."
        "\n- Keep the tone professional, neutral, and concise."
        "\n- Do not add a sign-off or signature; the user will add those manually."
    )

    user_prompt = (
        f"Please draft a reply to the following email.\n\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n\n"
        f"--- Email Body ---\n{cleaned_body}\n--- End of Email ---\n\n"
        f"Draft a reply body only. Plain text. No subject. No markdown."
    )

    logger.info("Requesting draft from OpenAI model '%s' for subject: %s", model, subject)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=600,
        temperature=0.4,  # Slightly creative but mostly predictable
    )

    draft_text = response.choices[0].message.content.strip()
    logger.info("Draft generated successfully (%d chars)", len(draft_text))
    return draft_text, model
