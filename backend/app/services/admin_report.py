"""Weekly aggregate report; no private user IDs, revenue guesses or commercial URLs."""
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text


def report_period(now):
    local = now.astimezone(ZoneInfo("America/Sao_Paulo"))
    end = (local-timedelta(days=local.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return (end-timedelta(days=7)).astimezone(UTC), end.astimezone(UTC)


async def aggregate_report(session, now=None):
    start, end = report_period(now or datetime.now(UTC))
    params = {"start": start, "end": end}
    counts = dict((await session.execute(text("""
      SELECT
        (SELECT count(*) FROM products WHERE created_at>=:start AND created_at<:end) AS new_products,
        (SELECT count(DISTINCT product_id) FROM product_observations WHERE observed_at>=:start AND observed_at<:end) AS observed_products,
        (SELECT count(*) FROM publications WHERE published_at>=:start AND published_at<:end AND delivery_status='SENT') AS publications,
        (SELECT count(*) FROM coupon_publications WHERE published_at>=:start AND published_at<:end) AS coupon_publications,
        (SELECT count(*) FROM task_runs WHERE finished_at>=:start AND finished_at<:end AND status='FAILED') AS failures,
        (SELECT count(*) FROM publication_queue WHERE updated_at>=:start AND updated_at<:end AND status='skipped') AS skipped_queue
    """), params)).mappings().one())
    actions = [dict(row) for row in (await session.execute(text("""
      SELECT action,sum(count) AS count FROM bot_interaction_metrics
      WHERE day>=:start_day AND day<:end_day GROUP BY action ORDER BY count DESC
    """), {"start_day": start.astimezone(ZoneInfo('America/Sao_Paulo')).strftime('%Y-%m-%d'),
        "end_day": end.astimezone(ZoneInfo('America/Sao_Paulo')).strftime('%Y-%m-%d')})).mappings()]
    categories = [dict(row) for row in (await session.execute(text("""
      SELECT coalesce(p.category_name,'Sem categoria') AS category,count(*) AS count
      FROM publications pub JOIN publication_queue q ON q.id=pub.queue_id JOIN products p ON p.id=q.product_id
      WHERE pub.published_at>=:start AND pub.published_at<:end AND pub.delivery_status='SENT'
      GROUP BY p.category_name ORDER BY count DESC LIMIT 10
    """), params)).mappings()]
    return {"start": start.isoformat(), "end": end.isoformat(), "counts": counts,
        "bot_actions": actions, "categories": categories,
        "limitations": ["Rejeições representam linhas de fila atualizadas no período, não todas as decisões históricas.",
            "Conversão, comissão e economia realizada não estão disponíveis."]}


def render_report(payload):
    c = payload["counts"]
    return ("📊 RADAR DO JULIUS — RESUMO SEMANAL\n\n"
        f"Produtos novos: {c['new_products']}\nProdutos observados: {c['observed_products']}\n"
        f"Publicações: {c['publications']}\nCupons publicados: {c['coupon_publications']}\n"
        f"Filas descartadas/atualizadas: {c['skipped_queue']}\nFalhas de tarefas: {c['failures']}\n"
        f"Interações agregadas do bot: {sum(int(a['count']) for a in payload['bot_actions'])}\n\n"
        "Período: semana anterior completa (São Paulo).\n"
        "O painel contém as categorias e os detalhes. Conversão e comissão não são estimadas.")


async def prepare_report(session, now=None):
    now = now or datetime.now(UTC)
    start, end = report_period(now)
    key = start.strftime("%Y-%m-%d")
    existing = (await session.execute(text("SELECT delivery_status FROM weekly_admin_reports WHERE week_key=:key"), {"key": key})).scalar()
    if existing:
        return key, None
    payload = await aggregate_report(session, now)
    await session.execute(text("INSERT INTO weekly_admin_reports(week_key,payload) VALUES (:key,CAST(:payload AS json)) ON CONFLICT DO NOTHING"),
        {"key": key, "payload": json.dumps(payload, default=str)})
    return key, payload
