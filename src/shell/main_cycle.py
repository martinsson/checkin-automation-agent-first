"""
Main poll cycle — thin sequencer that composes pollers and handlers.

Replaces the old poll_once() function. The sleep loop lives in scripts/run.py.
"""

import logging

from src.agent import AgentRunner
from src.ports.cleaner import CleanerNotifier
from src.ports.intent import IntentClassifier
from src.ports.memory import RequestMemory
from src.ports.reservation_cache import ReservationCache
from src.ports.response import GuestAcknowledger, ReplyComposer, ResponseParser
from src.ports.smoobu import SmoobuGateway

from src.shell.pollers import cleaner_responses
from src.shell.handlers import draft_dispatch

log = logging.getLogger(__name__)


async def poll_cycle(
    *,
    smoobu: SmoobuGateway,
    memory: RequestMemory,
    cache: ReservationCache,
    classifier: IntentClassifier,
    acknowledger: GuestAcknowledger,
    parser: ResponseParser,
    composer: ReplyComposer,
    cleaner: CleanerNotifier,
    agent: AgentRunner,
    cleaner_name: str = "Marie",
    threads_cutoff_days: int = 7,
) -> None:
    """
    One full poll cycle: cleaner responses → dispatch reviewed drafts.

    Guest message detection is now handled by the HostBuddy webhook
    (POST /webhook/hostbuddy → src/web/hostbuddy_webhook.py).
    The guest_messages poller below is kept for reference but disabled.
    """
    # DISABLED: Guest messages — replaced by HostBuddy webhook trigger.
    # See src/web/hostbuddy_webhook.py
    #
    # from src.shell.pollers import guest_messages
    # from src.shell.handlers import guest_request
    # discovered = guest_messages.poll(smoobu, cache, threads_cutoff_days)
    # for item in discovered:
    #     try:
    #         result = await guest_request.handle(...)
    #     except Exception as exc:
    #         log.error("Handler error for reservation %d: %s", item.reservation_id, exc)

    # 1. Cleaner responses — feed into the agent
    try:
        responses = await cleaner_responses.poll(cleaner)
        for response in responses:
            try:
                # Look up the reservation_id from the request_id
                req = await memory.get_request(response.request_id)
                if req is None:
                    log.warning(
                        "Cleaner response for unknown request_id=%s — skipping",
                        response.request_id,
                    )
                    continue

                await agent.run(
                    reservation_id=req.reservation_id,
                    event_type="cleaner_reply",
                    event_payload={
                        "request_id": response.request_id,
                        "raw_text": response.raw_text,
                    },
                    request_id=req.request_id,
                    intent=req.intent,
                    guest_name=req.guest_name,
                    property_name=req.property_name,
                )
                log.info(
                    "Agent handled cleaner reply for reservation=%d request=%s",
                    req.reservation_id,
                    response.request_id,
                )
            except Exception as exc:
                log.error(
                    "Agent error for cleaner response req=%s: %s",
                    response.request_id,
                    exc,
                )
    except Exception as exc:
        log.error("Failed to poll cleaner responses: %s", exc)

    # 2. Dispatch reviewed drafts
    try:
        await draft_dispatch.run(
            memory=memory,
            smoobu=smoobu,
            cleaner=cleaner,
            cleaner_name=cleaner_name,
        )
    except Exception as exc:
        log.error("Failed to dispatch reviewed drafts: %s", exc)
