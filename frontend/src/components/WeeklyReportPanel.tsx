import { useEffect, useState } from "react";
import { api } from "../api";

type Report = Awaited<ReturnType<typeof api.weeklyReport>>;
const labels: Record<string, string> = { new_products: "Produtos novos", observed_products: "Produtos observados", publications: "Publicações", coupon_publications: "Cupons publicados", failures: "Falhas", skipped_queue: "Descartes atualizados" };

export function WeeklyReportPanel() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    api.weeklyReport().then(value => { if (active) setReport(value); }).catch((reason: Error) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, []);
  return <section className="rounded-xl border border-slate-200 bg-slate-50 p-4">
    <h4 className="text-lg font-bold">Resumo da semana anterior</h4>
    <p className="mt-1 text-sm text-slate-500">Dados agregados da operação. O relatório é enviado somente ao administrador.</p>
    {error ? <p role="alert">{error}</p> : !report ? <p role="status">Carregando resumo…</p> : <>
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3">{Object.entries(report.counts).map(([key, count]) => <div key={key} className="rounded-lg bg-white p-3"><strong className="block text-2xl">{count.toLocaleString("pt-BR")}</strong><span className="text-xs text-slate-600">{labels[key] ?? key}</span></div>)}</div>
      <details className="mt-4 text-sm"><summary className="cursor-pointer font-semibold">Categorias e limites do relatório</summary><ul className="mt-3 space-y-1">{report.categories.map(item => <li key={item.category}>{item.category}: {item.count} publicações</li>)}</ul>{report.limitations.map(item => <p key={item} className="mt-2 text-xs text-slate-500">{item}</p>)}</details>
    </>}
  </section>;
}
