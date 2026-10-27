import { clsx } from "clsx";

type StatusBadgeProps = { value: string };

const labels: Record<string, string> = {
  AUTHENTICATED: "Autenticado",
  PUBLISHED: "Publicado",
  READY: "Pronto",
  DRY_RUN: "Simulação pronta",
  SELECTED: "Selecionado",
  RETRY: "Nova tentativa",
  LINK_PENDING: "Link pendente",
  CREATIVE_PENDING: "Criativo pendente",
  FAILED: "Falha",
  SKIPPED: "Ignorado",
  PUBLISHING: "Publicando",
  DISCOVERED: "Descoberto",
  APPLICABLE: "Aplicável",
};

export function StatusBadge({ value }: StatusBadgeProps) {
  const positive = ["AUTHENTICATED", "PUBLISHED", "READY", "PRIVATE_SMOKE", "PUBLIC_AUTO", "EXCELENTE", "APPLICABLE"].includes(value);
  const warning = ["DRY_RUN", "SELECTED", "RETRY", "LINK_PENDING", "CREATIVE_PENDING"].includes(value);
  return (
    <span className={clsx("inline-flex rounded-full px-3 py-1 text-xs font-bold", positive && "bg-emerald-50 text-success", warning && "bg-amber-50 text-amber-700", !positive && !warning && "bg-slate-100 text-slate-600")}>{labels[value] ?? value.replaceAll("_", " ")}</span>
  );
}
