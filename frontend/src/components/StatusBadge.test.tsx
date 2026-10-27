import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StatusBadge } from "./StatusBadge";

describe("StatusBadge", () => {
  it("traduz o estado de regeneração sem apresentá-lo como falha", () => {
    const html = renderToStaticMarkup(<StatusBadge value="CREATIVE_PENDING" />);
    expect(html).toContain("Criativo pendente");
    expect(html).not.toContain("Falha");
  });

  it("traduz publicações concluídas", () => {
    expect(renderToStaticMarkup(<StatusBadge value="PUBLISHED" />)).toContain("Publicado");
  });
});
