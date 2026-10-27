import { useEffect, useRef, useState, type FormEvent } from "react";
import { Eye, Save } from "lucide-react";
import { api } from "../api";
import { useQueryState } from "../navigation";

type SettingsData = Record<string, Record<string, unknown>>;

const weightFields = [
  ["discount", "Desconto"], ["popularity", "Vendas"], ["rating", "Avaliação"],
  ["commission", "Comissão"], ["coupon", "Cupom"], ["bonus", "Bônus"],
  ["price_confidence", "Confiança"], ["freshness", "Atualização"],
] as const;

const filterFields = [
  ["minimum_discount", "Desconto mín. (%)", "0.1"],
  ["minimum_rating", "Avaliação mín.", "0.1"],
  ["minimum_sold_count", "Vendas mín.", "1"],
  ["minimum_commission", "Comissão mín. (%)", "0.1"],
  ["minimum_price", "Preço mín. (R$)", "0.01"],
  ["maximum_price", "Preço máx. (R$)", "0.01"],
] as const;

function optionalNumber(data: FormData, key: string): number | null {
  const value = String(data.get(key) ?? "").trim();
  return value === "" ? null : Number(value);
}

export function SettingsPanel() {
  const [section, setSection] = useQueryState("settings.section", "scoring");
  const [settings, setSettings] = useState<SettingsData>({});
  const [message, setMessage] = useState("");
  const [impact, setImpact] = useState<{ products_evaluated: number; approved: number; estimated_entering: number; estimated_leaving: number; creative_regeneration: number; rescheduled: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState<number>();
  const [dirty, setDirty] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [progress, setProgress] = useState("");
  const [retentionImpact, setRetentionImpact] = useState("");
  const formRef = useRef<HTMLFormElement>(null);
  useEffect(() => {
    Promise.all([api.settings(), api.settingsRevision()]).then(([values, state]) => {
      setSettings(values); setRevision(state.revision);
    }).catch((error: Error) => setMessage(error.message));
  }, []);
  useEffect(() => {
    if (!jobId) return;
    let active = true;
    const poll = async () => {
      try {
        const job = await api.reprocessing(jobId);
        if (!active) return;
        const result = job.result as { processed?: number; approved?: number; superseded_by?: string } | null;
        setProgress(`${job.state} • ${result?.processed ?? 0} produtos processados • ${result?.approved ?? 0} aprovados`);
        if (job.state === "COALESCED" && result?.superseded_by) setJobId(result.superseded_by);
        else if (["SUCCEEDED", "FAILED"].includes(job.state)) setJobId(null);
      } catch { if (active) setProgress("Não foi possível consultar o progresso. A operação permanece registrada."); }
    };
    void poll(); const timer = window.setInterval(() => void poll(), 4000);
    return () => { active = false; window.clearInterval(timer); };
  }, [jobId]);
  if (!settings.scoring || !settings.cadence) {
    return <p role="status" className="card">{message || "Carregando configurações…"}</p>;
  }
  const scoring = settings.scoring ?? {};
  const weights = (scoring.weights ?? {}) as Record<string, unknown>;
  const cadence = settings.cadence ?? {};
  const content = settings.content ?? {};
  const bot = settings.bot ?? {};
  const badOffers = settings.bad_offers ?? {};
  const channelExperience = settings.channel_experience ?? {};

  function valuesFrom(form: HTMLFormElement) {
    const data = new FormData(form);
    const nextWeights = Object.fromEntries(
      weightFields.map(([key]) => [key, Number(data.get(`weight_${key}`))]),
    );
    const nextScoring = {
      ...scoring,
      weights: { ...weights, ...nextWeights },
      minimum_score: Number(data.get("minimum_score")),
      ...Object.fromEntries(filterFields.map(([key]) => [key, optionalNumber(data, key)])),
    };
    const nextCadence = {
      ...cadence,
      discovery_minutes: Number(data.get("discovery_minutes")),
      publish_minutes: Number(data.get("publish_minutes")),
      daily_limit: Number(data.get("daily_limit")),
    };
    const nextContent = {
      ...content,
      persona: String(data.get("persona") ?? "economic_parent"),
      impact: String(data.get("impact") ?? "balanced"),
      emoji_min: Number(data.get("emoji_min")),
      emoji_max: Number(data.get("emoji_max")),
      publish_without_coupon: data.get("publish_without_coupon") === "on",
    };
    const nextBot = {
      ...bot,
      enabled: data.get("bot_enabled") === "on",
      promo_enabled: data.get("promo_enabled") === "on",
      promo_times: String(data.get("promo_times") ?? "10:30,15:30,20:30").split(",").map((value) => value.trim()).filter(Boolean),
      promo_text: String(data.get("promo_text") ?? "").trim(),
      rate_per_minute: Number(data.get("rate_per_minute")),
      rate_per_day: Number(data.get("rate_per_day")),
    };
    const nextBadOffers = {
      ...badOffers,
      enabled: data.get("bad_offers_enabled") === "on",
      daily_limit: Number(data.get("bad_offers_daily_limit")),
      times: String(data.get("bad_offers_times") ?? "12:30,19:30").split(",").map((value) => value.trim()).filter(Boolean),
      maximum_score: Number(data.get("bad_offers_maximum_score")),
      min_observations: Number(data.get("bad_offers_min_observations")),
      min_history_hours: Number(data.get("bad_offers_min_history_hours")),
      freshness_minutes: Number(data.get("bad_offers_freshness_minutes")),
      minimum_confidence: Number(data.get("bad_offers_minimum_confidence")) / 100,
      retry_window_minutes: Number(data.get("bad_offers_retry_window_minutes")),
      min_reasons: 2,
      repeat_days: Number(data.get("bad_offers_repeat_days")),
      alternative_min_score: Number(data.get("bad_offers_alternative_min_score")),
    };
    const nextChannelExperience = {
      ...channelExperience,
      manifest_enabled: true,
      manifest_text: String(data.get("manifest_text") ?? "").trim(),
      weekly_enabled: data.get("weekly_enabled") === "on",
      weekly_day: Number(data.get("weekly_day")),
      weekly_time: String(data.get("weekly_time") ?? "18:00"),
      weekly_text: String(data.get("weekly_text") ?? "").trim(),
      global_spacing_minutes: Number(data.get("global_spacing_minutes")),
      admin_report_enabled: data.get("admin_report_enabled") === "on",
    };
    const retention = { ...settings.retention, days: Number(data.get("retention_days")), enabled: data.get("retention_enabled") === "on" };
    return { scoring: nextScoring, cadence: nextCadence, content: nextContent, bot: nextBot, bad_offers: nextBadOffers, channel_experience: nextChannelExperience, retention };
  }

  async function previewImpact() {
    if (!formRef.current) return;
    setBusy(true); setMessage("Calculando impacto…");
    try { setImpact(await api.settingsImpact(valuesFrom(formRef.current))); setMessage("Simulação concluída sem alterar a operação."); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Falha ao simular"); }
    finally { setBusy(false); }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true); setMessage("Salvando em uma única transação…");
    const values = valuesFrom(event.currentTarget);
    try {
      const result = await api.updateSettingsBatch(values, revision);
      setSettings((current) => ({ ...current, ...values }));
      setImpact(null);
      setDirty(false); if (result.revision !== undefined) setRevision(result.revision);
      setJobId(result.reprocess_task_id); setMessage(result.detail);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Falha ao salvar");
    } finally { setBusy(false); }
  }

  return (
    <section aria-labelledby="settings-title">
      <p className="label">Parâmetros operacionais</p>
      <h2 id="settings-title" className="mt-2 text-4xl font-black">Configurações</h2>
      <p className="mt-3 text-sm text-slate-500" role="status">Revisão {revision ?? "…"} • {dirty ? "Alterações não salvas" : "Configurações salvas"}</p>
      {progress ? <p className="notice mt-3 bg-amber-50" role="status">{progress}</p> : null}
      <nav className="mt-6 flex flex-wrap gap-2" aria-label="Seções de configurações">{[["scoring","Seleção"],["cadence","Cadência"],["content","Conteúdo"],["editorial","Editorial"],["trust","Confiança"],["telegram","Telegram"],["data","Banco de dados"]].map(([key,label]) => <button key={key} type="button" className={section === key ? "button-primary" : "button-secondary"} aria-pressed={section === key} onClick={() => setSection(key)}>{label}</button>)}</nav>
      <form data-active-section={section} ref={formRef} className="settings-form mt-8 grid gap-6 pb-28 xl:grid-cols-2" onSubmit={save} onInvalidCapture={(event) => {
        const field = event.target as HTMLInputElement;
        const target = field.closest<HTMLElement>("[data-section]")?.dataset.section;
        if (target) setSection(target);
        requestAnimationFrame(() => field.focus());
      }} onChange={() => { setDirty(true); setImpact(null); }}>
        <fieldset data-section="scoring" className="card space-y-4">
          <legend className="text-lg font-bold">Seleção automática</legend>
          <label className="block"><span className="label">Score mínimo</span><input name="minimum_score" type="number" min="0" max="100" defaultValue={Number(scoring.minimum_score ?? 85)} className="mt-2 w-full rounded-xl border border-black/10 px-4 py-3" /></label>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {weightFields.map(([key, label]) => <label key={key}><span className="label">{label} (%)</span><input name={`weight_${key}`} type="number" min="0" max="100" step="0.1" defaultValue={Number(weights[key] ?? 0)} className="mt-2 w-full rounded-xl border border-black/10 px-3 py-3" /></label>)}
          </div>
          <p className="text-sm text-slate-500">Dados ausentes têm seus pesos redistribuídos; valores nulos não viram zero.</p>
        </fieldset>
        <fieldset data-section="scoring" className="card space-y-4">
          <legend className="text-lg font-bold">Filtros aprovados</legend>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {filterFields.map(([key, label, step]) => <label key={key}><span className="label">{label}</span><input name={key} type="number" min="0" step={step} defaultValue={scoring[key] == null ? "" : Number(scoring[key])} placeholder="Sem limite" className="mt-2 w-full rounded-xl border border-black/10 px-3 py-3" /></label>)}
          </div>
        </fieldset>
        <fieldset data-section="cadence" className="card grid grid-cols-1 gap-3 sm:grid-cols-3 xl:col-span-2">
          <legend className="text-lg font-bold sm:col-span-3">Cadência</legend>
          <label><span className="label">Descoberta (min)</span><input name="discovery_minutes" type="number" min="1" defaultValue={Number(cadence.discovery_minutes ?? 10)} className="mt-2 w-full rounded-xl border border-black/10 px-3 py-3" /></label>
          <label><span className="label">Publicação (min)</span><input name="publish_minutes" type="number" min="1" defaultValue={Number(cadence.publish_minutes ?? 15)} className="mt-2 w-full rounded-xl border border-black/10 px-3 py-3" /></label>
          <label><span className="label">Limite diário</span><input name="daily_limit" type="number" min="1" defaultValue={Number(cadence.daily_limit ?? 30)} className="mt-2 w-full rounded-xl border border-black/10 px-3 py-3" /></label>
        </fieldset>
        <fieldset data-section="content" className="card space-y-4 xl:col-span-2">
          <legend className="text-lg font-bold">Conteúdo dos anúncios</legend>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label><span className="label">Persona</span><select name="persona" defaultValue={String(content.persona ?? "economic_parent")} className="input mt-2"><option value="economic_parent">Pai econômico</option></select></label>
            <label><span className="label">Impacto</span><select name="impact" defaultValue={String(content.impact ?? "balanced")} className="input mt-2"><option value="balanced">Equilibrado</option><option value="refined">Sofisticado</option></select></label>
            <label><span className="label">Emojis mín.</span><input name="emoji_min" type="number" min="2" max="8" defaultValue={Number(content.emoji_min ?? 4)} className="input mt-2" /></label>
            <label><span className="label">Emojis máx.</span><input name="emoji_max" type="number" min="3" max="10" defaultValue={Number(content.emoji_max ?? 7)} className="input mt-2" /></label>
          </div>
          <label className="flex items-center gap-3 rounded-2xl bg-slate-50 p-4"><input name="publish_without_coupon" type="checkbox" defaultChecked={content.publish_without_coupon !== false} className="h-5 w-5" /><span><strong>Publicar sem cupom confirmado</strong><span className="block text-sm text-slate-500">O anúncio continua usando preço e desconto revalidados.</span></span></label>
        </fieldset>
        <fieldset data-section="editorial" className="card space-y-4 xl:col-span-2">
          <legend className="text-lg font-bold">Análises de ofertas que não compensam</legend>
          <p className="text-sm text-slate-500">São publicadas somente com histórico e motivos financeiros suficientes. A cota nunca é preenchida artificialmente.</p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label><span className="label">Máximo por dia</span><input name="bad_offers_daily_limit" type="number" min="0" max="5" defaultValue={Number(badOffers.daily_limit ?? 2)} className="input mt-2"/></label>
            <label><span className="label">Horários</span><input name="bad_offers_times" defaultValue={Array.isArray(badOffers.times) ? badOffers.times.join(",") : "12:30,19:30"} className="input mt-2"/></label>
            <label><span className="label">Score máximo</span><input name="bad_offers_maximum_score" type="number" min="0" max="100" defaultValue={Number(badOffers.maximum_score ?? 45)} className="input mt-2"/></label>
            <label><span className="label">Observações mínimas</span><input name="bad_offers_min_observations" type="number" min="3" defaultValue={Number(badOffers.min_observations ?? 3)} className="input mt-2"/></label>
            <label><span className="label">Histórico mínimo (h)</span><input name="bad_offers_min_history_hours" type="number" min="24" defaultValue={Number(badOffers.min_history_hours ?? 24)} className="input mt-2"/></label>
            <label><span className="label">Frescor máximo (min)</span><input name="bad_offers_freshness_minutes" type="number" min="5" defaultValue={Number(badOffers.freshness_minutes ?? 30)} className="input mt-2"/></label>
            <label><span className="label">Confiança mínima (%)</span><input name="bad_offers_minimum_confidence" type="number" min="0" max="100" defaultValue={Number(badOffers.minimum_confidence ?? 0.7) * 100} className="input mt-2"/></label>
            <label><span className="label">Janela de nova tentativa (min)</span><input name="bad_offers_retry_window_minutes" type="number" min="15" max="180" defaultValue={Number(badOffers.retry_window_minutes ?? 90)} className="input mt-2"/></label>
            <label><span className="label">Repetição (dias)</span><input name="bad_offers_repeat_days" type="number" min="1" defaultValue={Number(badOffers.repeat_days ?? 14)} className="input mt-2"/></label>
            <label><span className="label">Score da alternativa</span><input name="bad_offers_alternative_min_score" type="number" min="0" max="100" defaultValue={Number(badOffers.alternative_min_score ?? 85)} className="input mt-2"/></label>
          </div>
          <label className="flex items-center gap-3 rounded-2xl bg-rose-50 p-4"><input name="bad_offers_enabled" type="checkbox" defaultChecked={badOffers.enabled !== false}/><span><strong>Ativar alertas financeiros</strong><span className="block text-sm text-slate-500">Nunca inclui link para o produto reprovado.</span></span></label>
        </fieldset>
        <fieldset data-section="trust" className="card space-y-4 xl:col-span-2">
          <legend className="text-lg font-bold">Confiança e experiência do canal</legend>
          <label className="flex items-center gap-2"><input name="admin_report_enabled" type="checkbox" defaultChecked={channelExperience.admin_report_enabled !== false}/>Relatório semanal no privado do administrador</label>
          <div className="grid gap-3 sm:grid-cols-3"><label><span className="label">Intervalo global (min)</span><input name="global_spacing_minutes" type="number" min="15" defaultValue={Number(channelExperience.global_spacing_minutes ?? 15)} className="input mt-2"/></label><label><span className="label">Dia do lembrete (1–7)</span><input name="weekly_day" type="number" min="1" max="7" defaultValue={Number(channelExperience.weekly_day ?? 7)} className="input mt-2"/></label><label><span className="label">Horário semanal</span><input name="weekly_time" type="time" defaultValue={String(channelExperience.weekly_time ?? "18:00")} className="input mt-2"/></label></div>
          <label className="flex items-center gap-2"><input name="weekly_enabled" type="checkbox" defaultChecked={channelExperience.weekly_enabled !== false}/>Lembrete semanal ativo</label>
          <label className="block"><span className="label">Manifesto personalizado (vazio usa o texto profissional padrão)</span><textarea name="manifest_text" defaultValue={String(channelExperience.manifest_text ?? "")} rows={5} className="input mt-2 min-h-32 resize-y"/></label>
          <label className="block"><span className="label">Lembrete semanal personalizado</span><textarea name="weekly_text" defaultValue={String(channelExperience.weekly_text ?? "")} rows={3} className="input mt-2 min-h-24 resize-y"/></label>
        </fieldset>
        <fieldset data-section="telegram" className="card space-y-4 xl:col-span-2">
          <legend className="text-lg font-bold">Atendimento privado e divulgação do bot</legend>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4"><label><span className="label">Horários das chamadas</span><input name="promo_times" defaultValue={Array.isArray(bot.promo_times) ? bot.promo_times.join(",") : "10:30,15:30,20:30"} className="input mt-2" placeholder="10:30,15:30,20:30"/></label><label><span className="label">Limite por minuto</span><input name="rate_per_minute" type="number" min="1" max="60" defaultValue={Number(bot.rate_per_minute ?? 10)} className="input mt-2"/></label><label><span className="label">Limite diário</span><input name="rate_per_day" type="number" min="10" max="1000" defaultValue={Number(bot.rate_per_day ?? 100)} className="input mt-2"/></label><div className="grid gap-2"><label className="flex items-center gap-2"><input name="bot_enabled" type="checkbox" defaultChecked={bot.enabled !== false}/>Atendimento ativo</label><label className="flex items-center gap-2"><input name="promo_enabled" type="checkbox" defaultChecked={bot.promo_enabled !== false}/>Chamadas no canal</label></div></div>
          <label className="block"><span className="label">Texto personalizado (opcional)</span><textarea name="promo_text" defaultValue={String(bot.promo_text ?? "")} rows={4} className="input mt-2 min-h-28 resize-y" placeholder="Deixe vazio para alternar os textos profissionais. Use {BOT_LINK} onde o link do bot deve aparecer."/><span className="mt-2 block text-xs text-slate-500">O marcador {"{BOT_LINK}"} é substituído pelo link oficial do atendimento privado.</span></label>
        </fieldset>
        <fieldset data-section="data" className="card space-y-4 xl:col-span-2">
          <legend className="text-lg font-bold">Retenção e preservação de evidências</legend>
          <p className="text-sm text-slate-500">A manutenção diária consolida observações antigas em resumos. Publicações, auditoria e evidências de produtos publicados são preservadas. A rotina trabalha em lotes limitados.</p>
          <label className="block"><span className="label">Manter detalhe por (dias)</span><input name="retention_days" type="number" min="90" max="3650" defaultValue={Number(settings.retention?.days ?? 90)} className="input mt-2"/></label>
          <label className="flex items-center gap-2"><input name="retention_enabled" type="checkbox" defaultChecked={settings.retention?.enabled !== false}/>Ativar manutenção diária às 03h15 (São Paulo)</label>
          <button type="button" className="button-secondary" onClick={() => { setRetentionImpact("Calculando com a configuração salva…"); void api.retentionPreview().then(value => setRetentionImpact(`${value.observations_selected} observações em ${value.days_selected} grupos diários no próximo lote. Nenhum dado foi removido.`)).catch((error: Error) => setRetentionImpact(error.message)); }}>Consultar próximo lote (sem excluir)</button>
          {retentionImpact ? <p role="status" className="text-sm">{retentionImpact}</p> : null}
        </fieldset>
        {impact ? <aside className="card border-amber-200 bg-amber-50 xl:col-span-2"><p className="label text-amber-800">Prévia de impacto</p><div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-5"><Impact label="Produtos avaliados" value={impact.products_evaluated}/><Impact label="Elegíveis estimados" value={impact.approved}/><Impact label="Entram na fila" value={impact.estimated_entering}/><Impact label="Saem da fila" value={impact.estimated_leaving}/><Impact label="Criativos a regenerar" value={impact.creative_regeneration}/></div></aside> : null}
        <div className="fixed inset-x-0 bottom-0 z-40 grid grid-cols-2 items-center gap-2 border-t border-slate-200 bg-white/95 p-3 shadow-2xl backdrop-blur sm:flex sm:flex-wrap sm:gap-3 sm:p-4 lg:left-[280px] xl:px-10">
          <button className="button-primary text-sm" disabled={busy}><Save size={16} aria-hidden="true" />Salvar alterações</button>
          <button type="button" className="button-secondary text-sm" disabled={busy} onClick={() => void previewImpact()}><Eye size={16}/>Simular impacto</button>
          {message ? <p role="status" className="text-sm text-slate-600">{message}</p> : null}
        </div>
      </form>
    </section>
  );
}

function Impact({ label, value }: { label: string; value: number }) {
  return <div><strong className="text-2xl text-slate-950">{value}</strong><span className="block text-xs text-slate-600">{label}</span></div>;
}
