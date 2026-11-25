/* Optional progressive enhancement. Embed inline after the report body. */
(() => {
  "use strict";
  const decode = encoded => {
    if (!encoded) return {};
    const bytes = Uint8Array.from(atob(encoded), character => character.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes));
  };
  const UI = decode(document.body.dataset.uiLabels);
  const ui = (key, values = {}) => Object.entries(values).reduce(
    (label, [name, value]) => label.replaceAll(`{${name}}`, String(value)), UI[key]);
  const temporalValue = raw => Date.parse(
    /^\d{4}-\d{2}-\d{2}[T ]/.test(raw) && !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw) ? `${raw}Z` : raw);
  document.querySelectorAll(".section-body").forEach(body => {
    if (body.querySelector(":scope > .prose-grid")) return;
    let run = [];
    const flush = () => {
      if (!run.length) return;
      const wrapper = document.createElement("div");
      wrapper.className = "prose-grid";
      body.insertBefore(wrapper, run[0]);
      run.forEach(block => wrapper.append(block));
      run = [];
    };
    [...body.children].forEach(block => {
      if (block.matches("p, .callout")) run.push(block);
      else flush();
    });
    flush();
  });

  const explorers = [...document.querySelectorAll("[data-report-explorer]")];
  explorers.forEach(root => {
    const buttons = [...root.querySelectorAll("[data-view-target]")];
    const panels = [...root.querySelectorAll("[data-report-view]")];
    if (!buttons.length || !panels.length) return;
    const selected = buttons.find(b => b.getAttribute("aria-pressed") === "true") || buttons[0];
    function show(id) {
      if (!panels.some(p => p.id === id)) return;
      panels.forEach(panel => { panel.hidden = panel.id !== id; });
      buttons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.viewTarget === id)));
      const status = root.querySelector("[data-view-status]");
      const active = buttons.find(b => b.dataset.viewTarget === id);
      if (status && active) status.textContent = ui("showing_view", { label: active.textContent.trim() });
    }
    buttons.forEach(button => button.addEventListener("click", () => show(button.dataset.viewTarget)));
    root.querySelectorAll("[data-reset-view]").forEach(button => button.addEventListener("click", () => show(selected.dataset.viewTarget)));
    show(selected.dataset.viewTarget);
    root.querySelectorAll("[data-view-controls]").forEach(controls => { controls.hidden = false; });
  });

  document.querySelectorAll("[data-report-filters]").forEach(root => {
    const section = root.closest(".report-section");
    const selects = [...root.querySelectorAll("[data-report-filter]")];
    const status = root.querySelector("[data-filter-status]");
    if (!section || !selects.length) return;
    const apply = () => {
      const filters = Object.fromEntries(selects.map(select => [select.dataset.filterId, select.value]));
      const active = selects.filter(select => select.value);
      if (status) status.textContent = active.length
        ? ui("filters_applied", { count: active.length })
        : UI.showing_all_section_data;
      section.dispatchEvent(new CustomEvent("report:filters-changed", { detail: { filters } }));
    };
    selects.forEach(select => select.addEventListener("change", apply));
    root.querySelector("[data-reset-filters]")?.addEventListener("click", () => {
      selects.forEach(select => { select.selectedIndex = 0; });
      apply();
    });
    apply();
    root.hidden = false;
  });

  document.querySelectorAll("[data-report-table]").forEach(root => {
    const table = root.querySelector("table");
    const body = table?.tBodies[0];
    if (!body) return;
    const rows = [...body.rows];
    const search = root.querySelector("[data-table-search]");
    const count = root.querySelector("[data-table-status]");
    const section = root.closest(".report-section");
    const drilldown = root.closest("[data-report-drilldown]");
    const drilldownStatus = root.querySelector("[data-drilldown-status]");
    const sortButtons = [...table.querySelectorAll("[data-sort-column]")];
    const collator = new Intl.Collator(document.documentElement.lang || undefined,
      { numeric: true, sensitivity: "base" });
    const declaredFilterIds = decode(root.dataset.filterIds);
    const applicableFilterIds = new Set(Array.isArray(declaredFilterIds) ? declaredFilterIds : []);
    let sectionFilters = Object.fromEntries(
      [...(section?.querySelectorAll("[data-report-filter]") || [])]
        .map(select => [select.dataset.filterId, select.value]));
    let drilldownValue = null;
    function filter() {
      const query = (search?.value || "").trim().toLocaleLowerCase();
      rows.forEach(row => {
        const values = decode(row.dataset.filterValues);
        const matchesSection = Object.entries(sectionFilters)
          .filter(([filterId]) => applicableFilterIds.has(filterId)).every(
          ([filterId, value]) => !value || values[filterId] === value);
        const matchesDrilldown = drilldownValue === null || values.__drilldown__ === drilldownValue;
        row.hidden = !matchesSection || !matchesDrilldown || !row.textContent.toLocaleLowerCase().includes(query);
      });
      const visible = rows.filter(row => !row.hidden).length;
      if (count) count.textContent = visible
        ? ui("rows_shown", { visible, total: rows.length })
        : UI.no_matching_rows;
    }
    sortButtons.forEach(button => {
      button.disabled = false;
      button.addEventListener("click", () => {
        const header = button.closest("th");
        const direction = header.getAttribute("aria-sort") === "ascending" ? "descending" : "ascending";
        const column = Number(button.dataset.sortColumn);
        const sortType = button.dataset.sortType;
        const factor = direction === "ascending" ? 1 : -1;
        const value = row => {
          const cell = row.cells[column];
          const raw = (cell?.dataset.value ?? cell?.textContent ?? "").trim();
          if (raw === "") return null;
          if (sortType === "number") return Number.isFinite(Number(raw)) ? Number(raw) : null;
          if (sortType === "date") {
            const parsed = temporalValue(raw);
            return Number.isFinite(parsed) ? parsed : null;
          }
          if (sortType === "boolean") return raw === "true" ? 1 : 0;
          return raw;
        };
        const sorted = [...rows].sort((a, b) => {
          const av = value(a), bv = value(b);
          if (av === null || bv === null) return av === bv ? 0 : av === null ? 1 : -1;
          return factor * (sortType === "text" ? collator.compare(av, bv) : av - bv);
        });
        table.querySelectorAll("th[aria-sort]").forEach(th => th.setAttribute("aria-sort", "none"));
        header.setAttribute("aria-sort", direction);
        sorted.forEach(row => body.append(row));
      });
    });
    search?.addEventListener("input", filter);
    root.querySelectorAll("[data-reset-table]").forEach(button => button.addEventListener("click", () => {
      if (search) search.value = "";
      rows.forEach(row => body.append(row));
      table.querySelectorAll("th[aria-sort]").forEach(th => th.setAttribute("aria-sort", "none"));
      filter();
    }));
    section?.addEventListener("report:filters-changed", event => {
      sectionFilters = event.detail?.filters || {};
      filter();
    });
    section?.addEventListener("report:drilldown", event => {
      if (!drilldown || drilldown.dataset.drilldownChart !== event.detail?.chart) return;
      drilldownValue = String(event.detail.category);
      if (drilldown.tagName === "DETAILS") drilldown.open = true;
      if (drilldownStatus) drilldownStatus.textContent = ui("showing_details", { value: drilldownValue });
      filter();
    });
    root.querySelectorAll("[data-reset-drilldown]").forEach(button => button.addEventListener("click", () => {
      drilldownValue = null;
      if (drilldownStatus) drilldownStatus.textContent = "";
      filter();
    }));
    root.querySelectorAll("[data-table-controls]").forEach(controls => { controls.hidden = false; });
    filter();
  });

  const navigation = document.querySelector(".section-nav");
  if (navigation && "IntersectionObserver" in window) {
    const links = [...navigation.querySelectorAll('a[href^="#section-"]')];
    const targets = links.map(link => document.querySelector(link.getAttribute("href"))).filter(Boolean);
    const select = id => links.forEach(link => {
      if (link.getAttribute("href") === `#${id}`) link.setAttribute("aria-current", "location");
      else link.removeAttribute("aria-current");
    });
    if (targets[0]) select(targets[0].id);
    const observer = new IntersectionObserver(entries => {
      const visible = entries.filter(entry => entry.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
      if (visible[0]) select(visible[0].target.id);
    }, { rootMargin: "-18% 0px -72% 0px", threshold: 0 });
    targets.forEach(target => observer.observe(target));
  }

})();
