import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Markdown, parseBlocks } from "./Markdown";

describe("Markdown", () => {
  it("renders bold, italic, inline code and headings without raw syntax", () => {
    const { container } = render(
      <Markdown content={"### Overview\nAtoms are **small** and *round*, see `H2O`."} />,
    );
    expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument();
    expect(container.querySelector("strong")).toHaveTextContent("small");
    expect(container.querySelector("em")).toHaveTextContent("round");
    expect(container.querySelector("code")).toHaveTextContent("H2O");
    expect(container.textContent).not.toContain("*");
    expect(container.textContent).not.toContain("#");
    expect(container.textContent).not.toContain("`");
  });

  it("renders ordered and unordered lists, keeping the model's numbering", () => {
    const { container } = render(
      <Markdown
        content={"Key points:\n\n1. **Chemical Bonding Theories**: octets.\n2. **VSEPR**: shapes.\n   - sub point\n\n- apple\n- pear"}
      />,
    );
    const ol = container.querySelector("ol")!;
    expect(ol.getAttribute("start")).toBe("1");
    const items = ol.querySelectorAll(":scope > li");
    expect(items).toHaveLength(2);
    expect(items[0].querySelector("strong")).toHaveTextContent("Chemical Bonding Theories");
    expect(items[1].querySelector("ul li")).toHaveTextContent("sub point");
    const topUl = container.querySelector(":scope > div > ul")!;
    expect(topUl.querySelectorAll("li")).toHaveLength(2);
    expect(container.textContent).not.toContain("**");
  });

  it("renders a citation marker inside bold as a clickable element", () => {
    const onClick = vi.fn();
    render(
      <Markdown
        content={"1. **Ionic bonds [2]** form between ions."}
        renderCitation={(marker, _raw, key) => (
          <button key={key} type="button" onClick={() => onClick(marker)}>
            [{marker}]
          </button>
        )}
      />,
    );
    const btn = screen.getByRole("button", { name: "[2]" });
    expect(btn.closest("strong")).not.toBeNull();
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledWith(2);
  });

  it("renders an unclosed ** literally while streaming", () => {
    const { container } = render(<Markdown content={"1. **Chemical Bon"} />);
    expect(container.querySelector("strong")).toBeNull();
    expect(container.querySelector("li")).toHaveTextContent("**Chemical Bon");
  });

  it("never injects HTML from model output", () => {
    const { container } = render(
      <Markdown content={'**<script>alert("x")</script>** <img src=x onerror=alert(1)>'} />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain('<script>alert("x")</script>');
    expect(container.textContent).toContain("<img src=x onerror=alert(1)>");
  });

  it("does not treat snake_case or arithmetic as emphasis", () => {
    const { container } = render(<Markdown content={"use snake_case_name and 2 * 3 * 4"} />);
    expect(container.querySelector("em")).toBeNull();
    expect(container.textContent).toBe("use snake_case_name and 2 * 3 * 4");
  });

  it("wraps an annotation range spanning bold as one span", () => {
    const content = "Intro. **Hybridisation** mixes orbitals [1]. Tail.";
    const start = content.indexOf("**Hybridisation**");
    const end = content.indexOf(" Tail.");
    const { container } = render(
      <Markdown
        content={content}
        annotations={[{ start, end, data: "weak" }]}
        renderAnnotation={(data, children, key, isEnd) => (
          <span key={key} data-ann={data}>
            {children}
            {isEnd && "!"}
          </span>
        )}
      />,
    );
    const spans = container.querySelectorAll("[data-ann]");
    expect(spans).toHaveLength(1);
    expect(spans[0]).toHaveTextContent("Hybridisation mixes orbitals [1].!");
    expect(spans[0].querySelector("strong")).toHaveTextContent("Hybridisation");
  });

  it("parses block offsets against the raw source", () => {
    const src = "# Title\n\n- a\n- b";
    const blocks = parseBlocks(src);
    expect(blocks.map((b) => b.kind)).toEqual(["heading", "list"]);
    const h = blocks[0] as { start: number; end: number };
    expect(src.slice(h.start, h.end)).toBe("Title");
  });
});
