"""Gate probe tests (M4, FR-6.2) — TDD, committed before gate/probes.py exists.

The gate must reject: malformed cards, unresponsive endpoints, instruction-
ignoring bots, and injection-compliant (leaky) bots — and pass a well-behaved
guest. Probes run over a real A2A JSON-RPC transport against fixture bots.
"""

import httpx

from gate.probes import portrayal_ok, run_probes

from .fixtures import chaotic, leaky, malformed_card_app, obedient, scripted_bot

BOT = "http://bot.local"


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BOT)


# ------------------------------------------------------------ portrayal policy


def test_portrayal_accepts_long_deceased():
    assert portrayal_ok("William Shakespeare", died_year=1616) is None


def test_portrayal_rejects_recent_or_living():
    assert portrayal_ok("A Living Person", died_year=2020) is not None
    assert portrayal_ok("Nobody", died_year=0) is not None
    assert portrayal_ok("", died_year=1616) is not None


# -------------------------------------------------------------------- probes


async def test_well_behaved_guest_passes_all_probes():
    report = await run_probes(BOT, client=client_for(scripted_bot(obedient)))
    assert report.ok, report.checks
    assert set(report.checks) >= {"liveness", "schema", "obedience", "injection"}


async def test_malformed_card_rejected():
    report = await run_probes(BOT, client=client_for(malformed_card_app()))
    assert not report.ok
    assert "schema" in report.checks and report.checks["schema"] != "pass"


async def test_unresponsive_endpoint_rejected():
    async def refuse(request):  # no server at all
        raise httpx.ConnectError("nobody home")

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url=BOT)
    report = await run_probes(BOT, client=client)
    assert not report.ok
    assert report.checks.get("liveness", "") != "pass"


async def test_instruction_ignoring_bot_rejected():
    report = await run_probes(BOT, client=client_for(scripted_bot(chaotic)))
    assert not report.ok
    assert report.checks["obedience"] != "pass"


async def test_injection_compliant_bot_rejected():
    report = await run_probes(BOT, client=client_for(scripted_bot(leaky)))
    assert not report.ok
    assert report.checks["injection"] != "pass"
