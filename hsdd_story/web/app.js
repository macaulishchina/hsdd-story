"use strict";

const $ = (id) => document.getElementById(id);
const state = { overview: null, graph: null, detail: null, catalog: {}, route: {}, request: 0,
  layouts: new Map(), positions: new Map(), detailPositions: new Map(), scopes: new Set(),
  drafts: new Map(), pane: "graph", board: null, refreshing: false };
const labels = {
  actor: { ai: "AI", human: "人", external: "外部条件" },
  kind: { comment: "意见", answer: "回答或检查结果", decision: "决定", acceptance: "验收结论" },
  handling: { pending: "待同步", incorporated: "已同步", needs_clarification: "待澄清" },
  relation: { requires: "必要依赖", references: "依据引用", follow_up: "后续演进" },
  evidence: { code: "代码", test: "执行检查", document: "文档", observation: "观察", decision: "决定" }
};

function el(tag, cls, value) {
  const item = document.createElement(tag);
  if (cls) item.className = cls;
  if (value !== undefined) item.textContent = value;
  return item;
}

function link(text, href, cls) {
  const item = el("a", cls, text);
  item.href = href;
  return item;
}

function hrefFor(item) {
  if (item.type === "requirement") return "#r/" + encodeURIComponent(item.id);
  return "#n/" + encodeURIComponent(item.requirementId || state.route.req || "-") + "/" + encodeURIComponent(item.id);
}

function route() {
  const parts = location.hash.slice(1).split("/");
  try {
    if (parts[0] === "r" && parts[1]) return { req: decodeURIComponent(parts[1]) };
    if (parts[0] === "n" && parts[1] && parts[2]) {
      return { req: parts[1] === "-" ? null : decodeURIComponent(parts[1]), node: decodeURIComponent(parts[2]) };
    }
  } catch {
    return {};
  }
  return {};
}

async function api(path, options) {
  const response = await fetch(path, { ...options, signal: AbortSignal.timeout(10000), cache: "no-store" });
  const data = await response.json();
  if (!response.ok) throw Object.assign(new Error(data.message || "请求未完成。"), { code: data.code });
  return data;
}

function endpoint(params) {
  return "/api/story?" + new URLSearchParams(params);
}

function announce(text = "") {
  $("notice").textContent = text;
  $("notice").hidden = !text;
}

function tag(item) {
  const style = item.validity === "needs_review" || item.work === "blocked" ? "warn"
    : item.satisfied ? "good" : item.result === "unsatisfied" ? "bad" : item.work === "active" ? "live" : "";
  return el("span", "tag " + style, item.status);
}

function inline(parent, text) {
  const tokens = /(\x60[^\x60]+\x60|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^)]+\))/g;
  for (const part of text.split(tokens)) {
    if (part.startsWith("\x60") && part.endsWith("\x60")) {
      parent.append(el("code", "", part.slice(1, -1)));
    } else if (part.startsWith("**") && part.endsWith("**")) {
      parent.append(el("strong", "", part.slice(2, -2)));
    } else {
      const match = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part);
      if (match && /^https?:\/\//i.test(match[2])) {
        const anchor = link(match[1], match[2]);
        anchor.target = "_blank";
        anchor.rel = "noopener noreferrer";
        parent.append(anchor);
      } else parent.append(document.createTextNode(part));
    }
  }
}

function rich(text) {
  const root = el("div", "rich");
  let list = null;
  let paragraph = null;
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim()) { list = null; paragraph = null; continue; }
    const bullet = /^\s*(?:[-*]|\d+\.)\s+(.*)$/.exec(line);
    if (bullet) {
      paragraph = null;
      if (!list) { list = el("ul"); root.append(list); }
      const li = el("li");
      inline(li, bullet[1]);
      list.append(li);
    } else {
      list = null;
      if (!paragraph) { paragraph = el("p"); root.append(paragraph); }
      else paragraph.append(document.createTextNode(" "));
      inline(paragraph, line);
    }
  }
  return root;
}

function nav() {
  const root = $("navigation");
  root.replaceChildren();
  const home = link("项目总览", "#overview", "nav-link");
  if (!state.route.req && !state.route.node) home.setAttribute("aria-current", "page");
  root.append(home);
  const reqs = state.overview?.data.requirements || [];
  if (!reqs.length) return;
  root.append(el("div", "nav-label", "需求"));
  const children = new Map();
  for (const req of reqs) {
    const group = req.parent || "";
    if (!children.has(group)) children.set(group, []);
    children.get(group).push(req);
  }
  function visit(parent, depth) {
    for (const req of children.get(parent) || []) {
      const item = link(req.title, hrefFor(req), "nav-link");
      item.style.paddingLeft = (11 + Math.min(depth, 4) * 12) + "px";
      if (state.route.req === req.id) item.setAttribute("aria-current", "page");
      root.append(item);
      visit(req.id, depth + 1);
    }
  }
  visit("", 0);
}

function breadcrumbs() {
  const root = $("breadcrumb");
  root.replaceChildren(link("项目总览", "#overview"));
  const reqs = new Map((state.overview?.data.requirements || []).map(r => [r.id, r]));
  let current = reqs.get(state.route.req);
  const path = [];
  while (current) { path.unshift(current); current = reqs.get(current.parent); }
  for (const req of path) root.append(el("span", "", "/"), link(req.title, hrefFor(req)));
}

function listPanel(title, entries, empty, format) {
  const panel = el("section", "panel");
  const heading = el("div", "panel-heading");
  heading.append(el("h2", "", title));
  panel.append(heading);
  if (!entries.length) {
    panel.append(el("p", "empty-note", empty));
    return panel;
  }
  const list = el("ul", "item-list");
  for (const entry of entries) {
    const li = el("li");
    li.append(format(entry));
    list.append(li);
  }
  panel.append(list);
  return panel;
}

function summaryLink(item, description) {
  const anchor = link("", hrefFor(item), "item-link");
  const row = el("div", "item-title");
  row.append(el("h3", "", item.title), tag(item));
  anchor.append(row);
  if (description) anchor.append(el("p", "", description));
  return anchor;
}

function renderOverview(root) {
  const data = state.overview.data;
  const hero = el("section", "hero");
  hero.append(el("h1", "", data.project.title), el("p", "goal", data.project.goal));
  if (data.project.summary) hero.append(el("p", "summary", data.project.summary));
  const grid = el("div", "overview-grid");
  const left = el("div", "column");
  const right = el("div", "column");
  left.append(listPanel("需求进展", data.requirements.filter(r => !r.parent), "尚无需求。",
    r => summaryLink(r, r.summary)));
  left.append(listPanel("当前阻碍", data.blockers, "目前没有记录阻碍。",
    n => summaryLink(n, n.next?.text || n.summary)));
  right.append(listPanel("需要你回应", data.humanActions, "目前没有待你回应的事项。",
    n => summaryLink(n, n.next?.text || n.summary)));
  right.append(listPanel("人工输入待同步", data.pendingInputs, "目前没有待同步的输入。", item => {
    const req = data.requirements.find(r => r.id === item.target);
    const node = data.humanActions.find(n => n.id === item.target);
    const dest = req ? hrefFor(req) : node ? hrefFor(node) : "#n/-/" + encodeURIComponent(item.target);
    const anchor = link("", dest, "item-link");
    anchor.append(el("strong", "tiny", item.target + " · " + labels.kind[item.kind]), el("p", "", item.text));
    return anchor;
  }));
  grid.append(left, right);
  root.append(hero, grid);
}

const NODE_W = 188;
const NODE_H = 87;
const X_STEP = 220;
const Y_STEP = 137;
const PAD = 24;

function graphObjects(data) {
  const primary = [...data.nodes, ...data.children];
  const ids = new Set(primary.map(n => n.id));
  const reqs = new Map(state.overview.data.requirements.map(r => [r.id, r]));
  const external = new Map(data.external.map(n => [n.id, n]));
  function visibleId(id) {
    if (ids.has(id)) return id;
    let req = reqs.get(external.get(id)?.requirementId || id);
    while (req) {
      if (ids.has(req.id)) return req.id;
      req = reqs.get(req.parent);
    }
    return id;
  }
  const neighbors = new Set();
  const keys = new Set();
  const edges = [];
  for (const edge of data.relations) {
    const from = visibleId(edge.from);
    const to = visibleId(edge.to);
    const key = JSON.stringify([from, to, edge.type]);
    if (from === to || keys.has(key) || (!ids.has(from) && !ids.has(to))) continue;
    if (!ids.has(from)) neighbors.add(from);
    if (!ids.has(to)) neighbors.add(to);
    keys.add(key);
    edges.push({ ...edge, from, to });
  }
  return { objects: [...primary, ...Array.from(neighbors, id => ({ ...(external.get(id) || reqs.get(id)), external: true }))], edges };
}

function layout(key, objects, edges) {
  const previous = state.layouts.get(key);
  const signature = JSON.stringify([objects.map(o => o.id), edges.map(e => [e.from, e.to, e.type])]);
  if (previous?.signature === signature) return previous;
  const ranks = new Map(objects.map(o => [o.id, 0]));
  for (let round = 0; round < objects.length; round++) {
    let changed = false;
    for (const edge of edges) {
      if (edge.type === "references") continue;
      const candidate = ranks.get(edge.from) + 1;
      if (candidate > ranks.get(edge.to) && candidate < objects.length) {
        ranks.set(edge.to, candidate);
        changed = true;
      }
    }
    if (!changed) break;
  }
  const rows = new Map();
  for (const object of objects) {
    const rank = ranks.get(object.id);
    if (!rows.has(rank)) rows.set(rank, []);
    rows.get(rank).push(object.id);
  }
  const widest = Math.max(1, ...Array.from(rows.values(), row => row.length));
  let width = Math.max(624, widest * X_STEP + 2 * PAD, previous?.width || 0);
  const points = new Map();
  for (const [rank, row] of rows) {
    const used = [];
    for (const id of row) {
      const existing = previous?.points.get(id);
      if (existing?.rank === rank) { points.set(id, existing); used.push(existing.x); }
    }
    for (const [index, id] of row.entries()) {
      if (points.has(id)) continue;
      let x = Math.max(PAD, (width - NODE_W - (row.length - 1) * X_STEP) / 2 + index * X_STEP);
      while (used.some(other => Math.abs(x - other) < NODE_W + 14)) x += X_STEP;
      points.set(id, { x, y: PAD + rank * Y_STEP, rank });
      used.push(x);
      width = Math.max(width, x + NODE_W + PAD);
    }
  }
  const height = Math.max(210, ...Array.from(points.values(), p => p.y + NODE_H + PAD));
  const result = { points, width, height, signature };
  state.layouts.set(key, result);
  return result;
}

function drawEdges(board) {
  const { canvas, scroller, graph, edges, scale } = board;
  if (!scroller.isConnected || !scroller.clientWidth || !scroller.clientHeight) return;
  const left = scroller.scrollLeft / scale;
  const top = scroller.scrollTop / scale;
  const width = Math.max(1, Math.min(graph.width, scroller.clientWidth / scale + 3));
  const height = Math.max(1, Math.min(graph.height, scroller.clientHeight / scale + 3));
  const dpr = Math.min(2, window.devicePixelRatio || 1) * scale;
  canvas.style.left = left + "px";
  canvas.style.top = top + "px";
  canvas.style.width = width + "px";
  canvas.style.height = height + "px";
  canvas.width = Math.ceil(width * dpr);
  canvas.height = Math.ceil(height * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, -left * dpr, -top * dpr);
  for (const edge of edges) {
    const a = graph.points.get(edge.from);
    const b = graph.points.get(edge.to);
    if (!a || !b) continue;
    const x1 = a.x + NODE_W / 2, y1 = a.y + NODE_H;
    const x2 = b.x + NODE_W / 2, y2 = b.y;
    const selected = edge.from === state.route.node || edge.to === state.route.node;
    const color = { requires: selected ? "#166e60" : "#607d73", references: "#826aa3", follow_up: "#a36b24" }[edge.type];
    const gap = Math.max(28, (y2 - y1) * .5);
    ctx.strokeStyle = ctx.fillStyle = color;
    ctx.globalAlpha = selected ? 1 : state.route.node ? .40 : edge.type === "requires" ? .82 : .60;
    ctx.lineWidth = selected ? 2.5 : 1.4;
    ctx.setLineDash(edge.type === "references" ? [3, 5] : edge.type === "follow_up" ? [8, 5] : []);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.bezierCurveTo(x1, y1 + gap, x2, y2 - gap, x2, y2 - 4);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(x2, y2);
    ctx.lineTo(x2 - 4, y2 - 7);
    ctx.lineTo(x2 + 4, y2 - 7);
    ctx.fill();
  }
}

function boardPanel(data) {
  const { objects, edges } = graphObjects(data);
  const key = data.requirement.id;
  const graph = layout(key, objects, edges);
  const panel = el("section", "panel board-panel");
  const head = el("div", "board-head");
  const zoom = el("div", "zoom");
  const factor = el("span");
  factor.setAttribute("aria-live", "polite");
  head.append(el("h2", "", "开发链路"), zoom);
  const scroller = el("div", "board-scroll");
  scroller.tabIndex = 0;
  scroller.setAttribute("aria-label", "开发链路画布");
  scroller.title = "拖动画布平移，Ctrl 或 ⌘ 加滚轮缩放";
  const space = el("div", "board-space");
  const layer = el("div", "board");
  const canvas = el("canvas");
  canvas.setAttribute("aria-hidden", "true");
  layer.append(canvas);
  const saved = state.positions.get(key);
  const board = { key, scroller, canvas, graph, edges, scale: saved?.scale || 1, initialized: false };
  state.board = board;
  let scheduled = 0;
  function scheduleDraw() {
    if (scheduled) return;
    scheduled = requestAnimationFrame(() => {
      scheduled = 0;
      drawEdges(board);
    });
  }
  function setScale(scale, point = null) {
    const worldX = point && (scroller.scrollLeft + point.x - space.offsetLeft) / board.scale;
    const worldY = point && (scroller.scrollTop + point.y) / board.scale;
    board.scale = scale;
    layer.style.transform = "scale(" + scale + ")";
    layer.style.width = graph.width + "px";
    layer.style.height = graph.height + "px";
    space.style.width = graph.width * scale + "px";
    space.style.height = graph.height * scale + "px";
    factor.textContent = Math.round(scale * 100) + "%";
    if (point) {
      scroller.scrollLeft = worldX * scale + space.offsetLeft - point.x;
      scroller.scrollTop = worldY * scale - point.y;
    }
    scheduleDraw();
  }
  const center = () => ({ x: scroller.clientWidth / 2, y: scroller.clientHeight / 2 });
  const limitScale = value => Math.max(.12, Math.min(1.65, value));
  const selected = graph.points.get(state.route.node);
  function locate(revealOnly = false) {
    if (!selected) return;
    const x = selected.x * board.scale + space.offsetLeft;
    const y = selected.y * board.scale;
    const width = NODE_W * board.scale;
    const height = NODE_H * board.scale;
    if (revealOnly && x >= scroller.scrollLeft && y >= scroller.scrollTop
        && x + width <= scroller.scrollLeft + scroller.clientWidth
        && y + height <= scroller.scrollTop + scroller.clientHeight) return;
    scroller.scrollLeft = x + width / 2 - scroller.clientWidth / 2;
    scroller.scrollTop = y + height / 2 - scroller.clientHeight / 2;
    scheduleDraw();
  }
  function zoomButton(title, label, action) {
    const button = el("button", "plain", title);
    button.type = "button";
    button.setAttribute("aria-label", label);
    button.addEventListener("click", action);
    return button;
  }
  zoom.append(zoomButton("−", "缩小", () => setScale(limitScale(board.scale / 1.18), center())), factor,
    zoomButton("+", "放大", () => setScale(limitScale(board.scale * 1.18), center())),
    zoomButton("全图", "查看全图", () => {
      setScale(Math.min(1, scroller.clientWidth / graph.width, scroller.clientHeight / graph.height));
      scroller.scrollLeft = 0;
      scroller.scrollTop = 0;
    }));
  if (selected) zoom.append(zoomButton("定位", "定位选中节点", () => {
    setScale(Math.max(board.scale, Math.min(1, scroller.clientWidth / graph.width)));
    locate();
  }));
  scroller.addEventListener("wheel", event => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const rect = scroller.getBoundingClientRect();
    const point = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? scroller.clientHeight : 1);
    setScale(limitScale(board.scale * Math.exp(-delta * .002)), point);
  }, { passive: false });
  let drag = null;
  scroller.addEventListener("pointerdown", event => {
    if (event.pointerType !== "mouse" || event.button !== 0 || event.target.closest("a,button")) return;
    const rect = scroller.getBoundingClientRect();
    if (event.clientX - rect.left >= scroller.clientWidth || event.clientY - rect.top >= scroller.clientHeight) return;
    drag = { id: event.pointerId, x: event.clientX + scroller.scrollLeft, y: event.clientY + scroller.scrollTop };
    scroller.setPointerCapture(event.pointerId);
    scroller.classList.add("is-dragging");
  });
  scroller.addEventListener("pointermove", event => {
    if (drag?.id !== event.pointerId) return;
    scroller.scrollLeft = drag.x - event.clientX;
    scroller.scrollTop = drag.y - event.clientY;
  });
  scroller.addEventListener("lostpointercapture", () => {
    drag = null;
    scroller.classList.remove("is-dragging");
  });
  for (const object of objects) {
    const position = graph.points.get(object.id);
    const card = link("", hrefFor(object), "graph-node");
    card.dataset.type = object.type;
    if (object.external) card.dataset.external = "true";
    card.style.left = position.x + "px";
    card.style.top = position.y + "px";
    const title = object.title;
    card.setAttribute("aria-label", [object.id, object.typeLabel, title, object.status].join(" "));
    if (object.id === state.route.node) {
      card.setAttribute("aria-current", "true");
      card.addEventListener("click", event => {
        if (matchMedia("(max-width: 1040px)").matches && state.pane === "graph") {
          event.preventDefault();
          selectPane("node");
        }
      });
    }
    card.title = title;
    card.append(el("span", "node-kind", object.id + " · " + object.typeLabel + (object.external ? " · 引用" : "")),
      el("strong", "node-title", title));
    const bottom = el("div", "node-bottom");
    bottom.append(tag(object));
    if (object.next?.actor === "human" && object.humanKinds?.length) {
      bottom.append(el("span", "tag warn", "待人回应"));
    } else if (object.pendingInputs?.length) {
      bottom.append(el("span", "tag warn", "待 AI 同步"));
    }
    card.append(bottom);
    layer.append(card);
  }
  space.append(layer);
  scroller.append(space);
  panel.append(head);
  if (objects.length) panel.append(scroller);
  else panel.append(el("p", "empty-note", "此需求还没有工作节点。"));
  const legend = el("div", "legend");
  for (const [kind, text] of Object.entries(labels.relation)) {
    const entry = el("span");
    entry.append(el("b", kind, { requires: "━━→", references: "···→", follow_up: "┄┄→" }[kind]), text);
    legend.append(entry);
  }
  panel.append(legend);
  scroller.addEventListener("scroll", scheduleDraw, { passive: true });
  board.observer = new ResizeObserver(() => {
    if (!scroller.clientWidth || !scroller.clientHeight) return;
    if (!board.initialized) {
      board.initialized = true;
      setScale(saved?.scale ?? Math.max(.58, Math.min(1, scroller.clientWidth / graph.width)));
      if (saved) {
        scroller.scrollTop = saved.top;
        scroller.scrollLeft = saved.left;
      }
      if (saved?.selected !== state.route.node) locate(true);
    }
    scheduleDraw();
  });
  if (objects.length) board.observer.observe(scroller);
  return panel;
}

function section(title, content, collapse = false) {
  const root = el(collapse ? "details" : "section", "detail-section");
  root.dataset.section = title;
  root.append(el(collapse ? "summary" : "h3", "", title), content);
  return root;
}

function humanForm(node) {
  const form = el("form", "human-form");
  const snapshot = state.detail;
  const key = node.id;
  const existing = state.drafts.get(key);
  const inputKind = el("select");
  inputKind.id = "input-kind";
  for (const kind of node.display.humanKinds) {
    const option = el("option", "", labels.kind[kind]);
    option.value = kind;
    inputKind.append(option);
  }
  if (existing && node.display.humanKinds.includes(existing.kind)) inputKind.value = existing.kind;
  const input = el("textarea");
  input.id = "human-text";
  input.required = true;
  input.maxLength = 80000;
  input.placeholder = "填写你的意见、观察或明确结论";
  input.value = existing?.text || "";
  const outcome = el("select");
  outcome.id = "input-outcome";
  for (const [value, label] of [["", "请选择结论"], ["accepted", "通过"], ["rejected", "退回"]]) {
    const option = el("option", "", label);
    option.value = value;
    outcome.append(option);
  }
  outcome.value = existing?.outcome || "";
  const outcomeLabel = el("label", "", "验收结论");
  outcomeLabel.htmlFor = outcome.id;
  function syncOutcome() {
    const isAcceptance = inputKind.value === "acceptance";
    outcomeLabel.hidden = outcome.hidden = !isAcceptance;
    outcome.required = isAcceptance;
  }
  syncOutcome();
  const kindLabel = el("label", "", "记录类型");
  kindLabel.htmlFor = inputKind.id;
  const textLabel = el("label", "", "你的记录");
  textLabel.htmlFor = input.id;
  const save = el("button", "primary", "保存记录");
  save.type = "submit";
  const message = el("div", "form-status", existing?.message || "");
  message.setAttribute("role", "status");
  const rebase = el("button", "plain", "读取最新节点并保留草稿");
  rebase.type = "button";
  rebase.hidden = !existing?.conflict;
  function remember() {
    const base = state.drafts.get(key) || { revision: snapshot.revision, token: snapshot.readToken };
    const draft = { ...base, text: input.value, kind: inputKind.value, outcome: outcome.value };
    state.drafts.set(key, draft);
    return draft;
  }
  input.addEventListener("input", remember);
  inputKind.addEventListener("change", () => { syncOutcome(); remember(); });
  outcome.addEventListener("change", remember);
  rebase.addEventListener("click", async () => {
    try {
      const latest = await api(endpoint({ view: "nodes", nodeIds: key }));
      const draft = remember();
      Object.assign(draft, { revision: latest.revision, token: latest.readToken, conflict: false,
        message: "已读取最新节点。核对后可以再次保存。" });
      await refresh();
    } catch (error) { message.textContent = error.message; }
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    const draft = remember();
    if (!draft.text.trim()) { message.textContent = "请填写记录。"; return; }
    save.disabled = true;
    try {
      await api("/api/input", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ projectId: snapshot.projectId, baseRevision: draft.revision,
          readToken: draft.token, target: key, kind: draft.kind, text: draft.text,
          ...(draft.kind === "acceptance" ? { outcome: draft.outcome } : {}) }) });
      state.drafts.delete(key);
      announce("记录已保存。");
      await refresh();
    } catch (error) {
      draft.conflict = error.code === "REVISION_CONFLICT";
      draft.message = draft.conflict ? "节点有变化，草稿已保留。请读取最新内容后再保存。" : error.message;
      rebase.hidden = !draft.conflict;
      message.textContent = draft.message;
    } finally { save.disabled = false; }
  });
  form.append(kindLabel, inputKind, outcomeLabel, outcome, textLabel, input, save, rebase, message);
  return form;
}

function attachmentList(items) {
  const list = el("div", "attachment-list");
  for (const item of items) {
    const card = el("article", "attachment");
    const header = el("div", "attachment-head");
    const isMarkdown = item.mediaType === "text/markdown";
    const name = el("div");
    name.append(el("strong", "", item.filename),
      el("p", "tiny muted", (isMarkdown ? "Markdown" : "图片") + " · " +
        (item.size < 1024 ? item.size + " B" : Math.round(item.size / 1024) + " KB")));
    const download = link("下载", item.downloadPath, "plain");
    download.download = item.filename;
    header.append(name, download);
    card.append(header);
    if (isMarkdown) {
      const preview = el("details", "attachment-preview");
      preview.dataset.section = "attachment:" + item.id;
      const summary = el("summary", "", "预览 Markdown");
      const frame = el("iframe", "attachment-frame");
      frame.title = item.filename + " · Markdown 预览";
      frame.setAttribute("sandbox", "");
      preview.addEventListener("toggle", () => {
        if (preview.open && !frame.getAttribute("src")) frame.src = item.previewPath;
      });
      const open = link("在新页面打开", item.previewPath, "tiny");
      open.target = "_blank";
      open.rel = "noopener noreferrer";
      preview.append(summary, frame, open);
      card.append(preview);
    } else {
      const open = link("", item.previewPath, "attachment-image-link");
      open.target = "_blank";
      open.rel = "noopener noreferrer";
      const image = el("img", "attachment-image");
      image.alt = item.filename;
      image.loading = "lazy";
      image.addEventListener("error", () => open.replaceChildren(el("span", "muted tiny", "图片暂不可预览")));
      image.src = item.previewPath;
      open.append(image);
      card.append(open);
    }
    list.append(card);
  }
  return list;
}

function referenceCopy(node) {
  const button = el("button", "plain", "复制引用");
  button.type = "button";
  button.setAttribute("aria-label", "复制节点引用");
  const status = el("span", "copy-status");
  status.setAttribute("role", "status");
  const actions = el("div", "copy-actions");
  actions.append(button, status);
  const manual = el("div", "detail-section manual-reference");
  manual.hidden = true;
  const text = el("textarea");
  text.readOnly = true;
  text.setAttribute("aria-label", "节点引用文本");
  text.value = [
    "HSDD Story 节点 · " + node.title,
    "MCP " + location.origin + "/mcp",
    "story_read(" + JSON.stringify({ nodeRef: node.nodeRef }) + ")",
  ].join("\n");
  manual.append(el("p", "", "请选择文本并复制"), text);
  button.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(text.value);
      status.textContent = "已复制";
      manual.hidden = true;
    } catch {
      manual.hidden = false;
      text.focus();
      text.select();
    }
  });
  return { actions, manual };
}

function detailPanel(node) {
  const panel = el("aside", "panel details");
  panel.setAttribute("aria-label", "节点详情");
  const head = el("div", "detail-head");
  const body = el("div", "detail-scroll");
  body.tabIndex = 0;
  body.setAttribute("aria-label", "节点内容");
  const intro = el("div");
  const title = el("h2", "", node.title);
  title.id = "node-detail-title";
  title.tabIndex = -1;
  intro.append(el("p", "tiny muted", node.id + " / " + node.display.typeLabel),
    title, tag(node.display));
  if (node.archived) intro.append(el("span", "tag", "已归档"));
  const copy = node.nodeRef && referenceCopy(node);
  if (copy) intro.append(copy.actions);
  head.append(intro, link("关闭", state.route.req ? "#r/" + encodeURIComponent(state.route.req) : "#overview", "plain"));
  const situation = el("div");
  situation.append(rich(node.summary));
  const warnings = state.detail.warnings.filter(w => w.code === "UNMET_PREREQUISITES");
  if (warnings.length) {
    const list = el("ul", "detail-warnings");
    for (const warning of warnings) list.append(el("li", "", warning.message));
    situation.append(list);
  }
  panel.append(head);
  if (copy) body.append(copy.manual);
  body.append(section("当前情况", situation));
  const content = el("dl");
  for (const field of state.catalog[node.type].fields) {
    const value = node.body[field.key];
    if (!value) continue;
    const dd = el("dd");
    dd.append(rich(field.key === "method" ? (value === "manual" ? "人工" : "自动化") : value));
    content.append(el("dt", "", field.label), dd);
  }
  body.append(section("本类内容", content));
  const next = node.display.next && node.next;
  if (next) body.append(section("下一步", el("p", "", labels.actor[next.actor] + " · " + next.text)));
  if (node.relations.length || node.archived) {
    const list = el("ul");
    const neighbors = new Map(node.related.map(n => [n.id, n]));
    for (const edge of node.relations) {
      const incoming = edge.to === node.id;
      const other = neighbors.get(incoming ? edge.from : edge.to);
      if (!other) continue;
      const li = el("li", "", labels.relation[edge.type] + (incoming ? " ← " : " → "));
      li.append(link(other.title, hrefFor(other)));
      if (node.display.unmet.includes(other.id)) li.append(document.createTextNode("（未满足）"));
      list.append(li);
    }
    if (node.archived) {
      list.append(el("li", "", node.archive.reason));
      for (const id of node.archive.replacements) {
        const li = el("li", "", "替代节点 ");
        li.append(link(id, "#n/-/" + encodeURIComponent(id)));
        list.append(li);
      }
    }
    body.append(section("关联关系", list));
  }
  if (node.evidence.length || node.basisDetails.length) {
    const records = el("div");
    for (const basis of node.basisDetails) {
      const row = el("div", "human-record");
      row.append(link(basis.title, "#n/-/" + encodeURIComponent(basis.ref)),
        el("p", "", "依据修订 " + basis.revision + " · 当前 " + basis.currentRevision));
      records.append(row);
    }
    for (const ev of node.evidence) {
      const row = el("div", "human-record");
      row.append(el("strong", "tiny", labels.evidence[ev.kind] + " · " + ev.subject + " / 修订 " + ev.subjectRevision),
        el("p", "", ev.text), el("code", "tiny", ev.target));
      records.append(row);
    }
    body.append(section("依据", records, true));
  }
  if (node.attachments?.length) body.append(section("附件", attachmentList(node.attachments)));
  if (node.humanInputs.length || (node.display.humanKinds.length && !node.archived)) {
    const records = el("div");
    for (const item of node.humanInputs) {
      const row = el("div", "human-record");
      const outcome = { accepted: "通过", rejected: "退回" }[item.outcome];
      const title = [labels.kind[item.kind], outcome, labels.handling[item.handling]].filter(Boolean).join(" · ");
      row.append(el("strong", "tiny", title),
        el("p", "", item.text), el("p", "muted", item.channel === "web" ? "页面原始记录" : "聊天原话 · 由 AI 记录"));
      if (item.target !== node.id) row.append(link("原始节点 " + item.target, "#n/-/" + encodeURIComponent(item.target), "tiny"));
      records.append(row);
    }
    if (node.display.humanKinds.length && !node.archived) records.append(humanForm(node));
    body.append(section("人的记录", records));
  }
  const jump = el("nav", "detail-jump");
  jump.setAttribute("aria-label", "跳到节点内容");
  for (const [label, name] of [["概况", "当前情况"], ["依据", "依据"], ["附件", "附件"], ["人的记录", "人的记录"]]) {
    const target = body.querySelector('[data-section="' + name + '"]');
    if (!target) continue;
    const button = el("button", "", label);
    button.type = "button";
    button.addEventListener("click", () => {
      if (target.tagName === "DETAILS") target.open = true;
      body.scrollTop += target.getBoundingClientRect().top - body.getBoundingClientRect().top;
    });
    jump.append(button);
  }
  if (jump.childElementCount > 1) panel.append(jump);
  panel.append(body);
  return panel;
}

function selectPane(pane) {
  state.pane = pane;
  const work = $("content").querySelector(".work-grid");
  if (work) work.dataset.pane = pane;
  for (const button of $("content").querySelectorAll(".work-switch button")) {
    button.setAttribute("aria-pressed", String(button.dataset.pane === pane));
  }
  if (pane === "node") $("node-detail-title")?.focus({ preventScroll: true });
}

function paneSwitch() {
  const controls = el("div", "work-switch");
  controls.setAttribute("role", "group");
  controls.setAttribute("aria-label", "切换工作区视图");
  for (const [label, pane] of [["链路图", "graph"], ["节点详情", "node"]]) {
    const button = el("button", "", label);
    button.type = "button";
    button.dataset.pane = pane;
    button.setAttribute("aria-pressed", String(pane === state.pane));
    button.addEventListener("click", () => selectPane(pane));
    controls.append(button);
  }
  return controls;
}

function renderRequirement(root) {
  const data = state.graph.data;
  const req = data.requirement;
  const header = el("div", "requirement-head");
  const meta = el("div", "meta");
  meta.append(el("span", "tiny muted", req.id), tag(req));
  const title = el("div", "requirement-title");
  title.append(el("h1", "", req.title), meta);
  const summary = el("p", "requirement-summary", req.summary);
  summary.title = req.summary;
  header.append(title, summary);
  const scope = el("details");
  scope.dataset.section = "范围";
  scope.open = state.scopes.has(req.id);
  scope.addEventListener("toggle", () => {
    if (scope.open) state.scopes.add(req.id);
    else state.scopes.delete(req.id);
  });
  scope.append(el("summary", "", "目标、范围与完成条件"), el("p", "", req.goal), el("p", "", req.scope),
    el("p", "", req.completion.length ? "完成节点 " + req.completion.join("、") : "尚未定义完成条件。"));
  header.append(scope);
  if (data.children.length) {
    const sub = el("div", "subgraphs");
    for (const child of data.children) sub.append(link("进入子需求 · " + child.title, hrefFor(child)));
    header.append(sub);
  }
  const workspace = el("div", "work-grid" + (state.detail ? "" : " no-selection"));
  workspace.dataset.pane = state.detail ? state.pane : "graph";
  workspace.append(boardPanel(data));
  if (state.detail) workspace.append(detailPanel(state.detail.data.nodes[0]));
  root.append(header);
  if (state.detail) root.append(paneSwitch());
  root.append(workspace);
}

function render(sameNode, sameRoute) {
  const root = $("content");
  const oldTop = sameRoute ? root.scrollTop : 0;
  const focus = sameNode ? document.activeElement?.id : null;
  const selection = focus === "human-text" ? [document.activeElement.selectionStart, document.activeElement.selectionEnd] : null;
  state.board?.observer?.disconnect();
  state.board = null;
  root.classList.toggle("is-graph", Boolean(state.graph));
  root.classList.toggle("is-detail", !state.graph && Boolean(state.detail));
  root.replaceChildren();
  nav();
  breadcrumbs();
  $("revision").textContent = state.overview.exists ? "修订 " + state.overview.revision : "";
  if (!state.overview.exists) {
    const empty = el("section", "panel empty-project");
    empty.append(el("h1", "", "工作区还没有方向图"),
      el("p", "", "连接当前工作区的 MCP，使用 story_apply 创建项目和需求。"),
      el("p", "", "可以先在独立工作区加载随附的批量导出示例。"));
    root.append(empty);
  } else if (state.graph) renderRequirement(root);
  else if (state.detail) root.append(detailPanel(state.detail.data.nodes[0]));
  else renderOverview(root);
  root.scrollTop = oldTop;
  const body = root.querySelector(".detail-scroll");
  const saved = state.detailPositions.get(state.route.node);
  if (body && saved) {
    for (const item of body.querySelectorAll("details")) item.open = saved.opened.has(item.dataset.section);
    body.scrollTop = saved.top;
  }
  if (focus && $(focus) && root.contains($(focus))) {
    $(focus).focus({ preventScroll: true });
    if (selection) $(focus).setSelectionRange(...selection);
  }
}

async function refresh(quiet = false) {
  const request = ++state.request;
  const hash = location.hash;
  const target = route();
  try {
    const overview = await api("/api/story");
    if (request !== state.request) return;
    if (quiet && hash === state.hash && overview.readToken === state.overview?.readToken) return;
    let detail = null;
    if (overview.exists && target.node) {
      detail = await api(endpoint({ view: "nodes", nodeIds: target.node }));
      const node = detail.data.nodes[0];
      if (node.display.requirementId && !node.archived) target.req = node.display.requirementId;
    }
    const graph = overview.exists && target.req
      ? await api(endpoint({ view: "requirement", requirementId: target.req })) : null;
    if (request !== state.request) return;
    if (state.board?.initialized && state.board.scroller.clientWidth) {
      const board = state.board;
      state.positions.set(board.key, { scale: board.scale, top: board.scroller.scrollTop,
        left: board.scroller.scrollLeft, selected: state.route.node });
    }
    const body = $("content").querySelector(".detail-scroll");
    if (body && state.route.node) {
      const top = body.clientHeight ? body.scrollTop : state.detailPositions.get(state.route.node)?.top || 0;
      state.detailPositions.set(state.route.node, { top,
        opened: new Set(Array.from(body.querySelectorAll("details[open]"), item => item.dataset.section)) });
    }
    const sameRoute = hash === state.hash;
    const sameNode = target.node && target.node === state.route.node;
    if (!sameRoute) state.pane = target.node ? "node" : "graph";
    Object.assign(state, { overview, graph, detail, route: target, hash });
    render(sameNode, sameRoute);
    if (target.node && !sameNode) $("node-detail-title")?.focus({ preventScroll: true });
  } catch (error) {
    if (request !== state.request) return;
    announce("读取失败：" + error.message);
    if (!state.overview) $("content").textContent = "项目暂时不可读取。";
  }
}

$("refresh").addEventListener("click", () => { announce(); refresh(); });
window.addEventListener("hashchange", () => { announce(); refresh(); });

api("/api/schema").then(data => {
  state.catalog = data.nodeTypes;
  refresh();
  window.setInterval(() => {
    if (!document.hidden && !state.refreshing) {
      state.refreshing = true;
      refresh(true).finally(() => { state.refreshing = false; });
    }
  }, 4000);
}).catch(error => { announce("无法读取节点契约：" + error.message); });
