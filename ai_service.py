"""
AI service: generates a professional email reply draft using OpenAI.

This module only DRAFTS a reply. It does not send anything.
"""

import os
import logging
from openai import OpenAI

logger = logging.getLogger(__name__)

# Prompt version string stored with each draft for auditing/iteration
PROMPT_VERSION = "v2-feedback"
MAX_BODY_CHARS = 4000


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
    settings=None,
    context=None,
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

    # Build dynamic instructions from user settings
    tone = "professional"
    custom_block = ""
    signature_block = ""
    footer_block = ""
    identity_block = ""
    business_context_block = ""
    hard_rules_block = ""
    escalation_block = ""
    example_block = ""
    category_block = ""
    length_instruction = "Keep the reply appropriately concise."
    thread_block = ""
    client_preferences_block = ""

    if settings:
        if settings.tone:
            tone = settings.tone
        if getattr(settings, "reply_length", None) == "concise":
            length_instruction = "Keep the reply very concise and efficient."
        elif getattr(settings, "reply_length", None) == "detailed":
            length_instruction = "Provide a more detailed reply when helpful, while staying clear and practical."
        if settings.sender_name or settings.company_name:
            identity_block = (
                f"\nYou are drafting on behalf of "
                f"{settings.sender_name or ''}"
                f"{' at ' + settings.company_name if settings.company_name else ''}."
            )
        if getattr(settings, "business_context", None):
            business_context_block = f"\nBusiness context: {settings.business_context}"
        if settings.custom_instructions:
            custom_block = f"\nAdditional instructions: {settings.custom_instructions}"
        if getattr(settings, "hard_rules", None):
            hard_rules_block = f"\nHard rules that must be followed: {settings.hard_rules}"
        if getattr(settings, "escalation_guidance", None):
            escalation_block = f"\nEscalation guidance: {settings.escalation_guidance}"
        if getattr(settings, "example_phrasing", None):
            example_block = f"\nPreferred writing style examples: {settings.example_phrasing}"
        if settings.footer_link and settings.footer_link_label:
            footer_block = (
                f"\nIf relevant, you may include this link naturally in the reply: "
                f"{settings.footer_link_label}: {settings.footer_link}"
            )
        if settings.signature:
            signature_block = f"\n\nAppend this exact signature at the end of every reply:\n{settings.signature}"
        if context and context.get("category"):
            category_prompt = getattr(settings, f"{context['category']}_prompt", None)
            if category_prompt:
                category_block = f"\nCategory-specific guidance for {context['category']} emails: {category_prompt}"

    if context:
        thread_bits = []
        if context.get("category"):
            thread_bits.append(f"Email category: {context['category']}.")
        if context.get("client_preferences"):
            client_preferences_block = f"\nClient style preferences: {context['client_preferences']}"
        if context.get("conversation_summary"):
            thread_bits.append(f"Conversation summary: {context['conversation_summary']}")
        if context.get("contact_summary"):
            thread_bits.append(f"Known contact summary: {context['contact_summary']}")
        if context.get("domain_summary"):
            thread_bits.append(f"Domain summary: {context['domain_summary']}")
        if context.get("recent_history"):
            thread_bits.append("Recent thread history:\n" + context["recent_history"])
        if thread_bits:
            thread_block = "\n\nContext:\n" + "\n".join(thread_bits)

    system_prompt = (
        f"You are a professional email assistant. "
        f"Your job is to draft a helpful, {tone}, and polite reply to an inbound email."
        f"{identity_block}"
        f"{business_context_block}"
        f"\n\nRules:"
        f"\n- Output ONLY the plain text body of the reply email."
        f"\n- Do NOT include a subject line."
        f"\n- Do NOT use markdown, bullet points, or any formatting symbols."
        f"\n- Do NOT make up facts, promises, or claims not supported by the inbound email."
        f"\n- If important information is missing, politely ask for clarification."
        f"\n- Keep the tone {tone}."
        f"\n- {length_instruction}"
        f"{custom_block}"
        f"{hard_rules_block}"
        f"{escalation_block}"
        f"{example_block}"
        f"{category_block}"
        f"{client_preferences_block}"
        f"{footer_block}"
        f"{signature_block}"
    )

    bounded_body = cleaned_body[:MAX_BODY_CHARS].strip()

    user_prompt = (
        f"Please draft a reply to the following email.\n\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n\n"
        f"--- Email Body ---\n{bounded_body}\n--- End of Email ---"
        f"{thread_block}\n\n"
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
