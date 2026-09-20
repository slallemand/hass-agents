"""CrewAI agents for consumption analysis (read-only reasoning)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from hass_agents.config import Settings
from hass_agents.schemas import ConsumptionReport, HouseConsumptionContext

logger = logging.getLogger(__name__)


def _configure_llm_env(settings: Settings) -> None:
    if settings.openai_api_key:
        os.environ["OPENAI_API_KEY"] = settings.openai_api_key
    if settings.openai_base_url:
        os.environ["OPENAI_API_BASE"] = settings.openai_base_url


def run_llm_crew(ctx: HouseConsumptionContext, settings: Settings) -> ConsumptionReport | None:
    """Run Data → Anomaly → Report crew. Returns None on failure."""
    try:
        from crewai import Agent, Crew, LLM, Process, Task
    except ImportError:
        logger.warning("crewai not installed — skipping LLM crew")
        return None

    _configure_llm_env(settings)
    model = settings.crewai_model
    logger.info("CrewAI LLM model=%s base_url=%s", model, settings.openai_base_url)
    llm = LLM(
        model=model,
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url,
        temperature=0.2,
    )

    context_json = ctx.model_dump_json()

    data_agent = Agent(
        role="Analyste données énergétiques",
        goal="Résumer factuellement les totaux et baselines sans inventer de chiffres",
        backstory=(
            "Tu es un analyste énergie domestique. Tu ne recalcules jamais les kWh: "
            "tu cites uniquement les valeurs fournies dans le JSON contexte."
        ),
        llm=llm,
        verbose=False,
        allow_delegation=False,
    )
    anomaly_agent = Agent(
        role="Détecteur d'anomalies",
        goal="Prioriser et expliquer les candidate_anomalies avec météo et présence",
        backstory=(
            "Tu expliques les écarts déjà détectés (z-score, delta%). "
            "Tu correlès avec T° ext, degree-days et occupation de la maison."
        ),
        llm=llm,
        verbose=False,
        allow_delegation=False,
    )
    report_agent = Agent(
        role="Rédacteur de rapports énergétiques",
        goal="Produire un rapport structuré JSON conforme au schéma demandé",
        backstory=(
            "Tu rédiges des rapports quotidien/hebdo/mensuel en français, clairs, "
            "actionnables, sans commander d'appareils."
        ),
        llm=llm,
        verbose=False,
        allow_delegation=False,
    )

    schema_hint = ConsumptionReport.model_json_schema()

    task_data = Task(
        description=(
            f"Période={ctx.period.value}. Voici le contexte JSON:\n{context_json}\n\n"
            "Résume en bullet points: totaux kWh/€, top devices, météo, présence. "
            "N'invente aucun chiffre."
        ),
        expected_output="Résumé factuel en français (bullet points).",
        agent=data_agent,
    )
    task_anomaly = Task(
        description=(
            "À partir du résumé et du contexte, priorise les anomalies candidates. "
            "Pour chaque anomalie importante: hypothèse plausible liée à météo/présence. "
            "Si aucune anomalie, dis-le clairement."
        ),
        expected_output="Liste priorisée d'anomalies avec hypothèses.",
        agent=anomaly_agent,
        context=[task_data],
    )
    task_report = Task(
        description=(
            "Produis UNIQUEMENT un JSON valide correspondant à ce schéma:\n"
            f"{json.dumps(schema_hint)}\n\n"
            "Contraintes:\n"
            f"- period doit être '{ctx.period.value}'\n"
            f"- period_start='{ctx.period_start.isoformat()}'\n"
            f"- period_end='{ctx.period_end.isoformat()}'\n"
            f"- generated_at='{ctx.generated_at.isoformat()}'\n"
            "- llm_used=true\n"
            "- narrative_md en français markdown court\n"
            "- suggested_checks = actions humaines (pas de commandes HA)\n"
            "- Réutilise findings/totals/top_devices du contexte (chiffres exacts)\n"
            "- headline + summary en français"
        ),
        expected_output="JSON ConsumptionReport valide",
        agent=report_agent,
        context=[task_data, task_anomaly],
        output_pydantic=ConsumptionReport,
    )

    crew = Crew(
        agents=[data_agent, anomaly_agent, report_agent],
        tasks=[task_data, task_anomaly, task_report],
        process=Process.sequential,
        verbose=False,
    )
    try:
        result = crew.kickoff()
        if getattr(result, "pydantic", None) is not None:
            report = result.pydantic
            if isinstance(report, ConsumptionReport):
                report.llm_used = True
                return report
        # Fallback parse raw
        raw = getattr(result, "raw", None) or str(result)
        return ConsumptionReport.model_validate_json(_extract_json(raw))
    except Exception:
        logger.exception("LLM crew failed")
        return None


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.startswith("```")]
        text = "\n".join(lines)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return text
