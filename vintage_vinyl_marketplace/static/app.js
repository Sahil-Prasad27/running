const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

const FORMAT_LABELS = {
  "12in_lp": '12" LP',
  "7in": '7" Single',
  "10in": '10"',
  "12in_maxi": '12" Maxi',
  "box_set": "Box Set",
  "picture_disc": "Picture Disc",
  "coloured": "Coloured Vinyl",
};

let cart = [];
let tradeRows = 0;

const money = (value) => "$" + Number(value || 0).toFixed(2);
const esc = (value) =>
  String(value ?? "").replace(/[&<>"]/g, (match) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
  })[match]);
const jsq = (value) => JSON.stringify(String(value ?? ""));

function toast(message, error = false) {
  const el = $("#toast");
  if (!el) return;
  el.textContent = message;
  el.className = "toast show" + (error ? " error" : "");
  window.setTimeout(() => {
    el.className = "toast";
  }, 3000);
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      "X-User-Id": "2",
      ...(options.headers || {}),
    },
    ...options,
  });
  const data = await response.json().catch(() => ({ message: response.statusText }));
  if (!response.ok || data.ok === false) {
    throw Object.assign(new Error(data.message || "Request failed"), data);
  }
  return data;
}

function debounced(fn, wait = 250) {
  let timeout;
  return (...args) => {
    window.clearTimeout(timeout);
    timeout = window.setTimeout(() => fn(...args), wait);
  };
}

function downloadFile(url, filename) {
  const link = document.createElement("a");
  link.href = url;
  if (filename) link.download = filename;
  link.style.display = "none";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function downloadDashboardExport(format) {
  downloadFile(`/api/dashboard/export?format=${encodeURIComponent(format)}`);
  toast(`Dashboard ${format.toUpperCase()} download started`);
}

function setReceiptDock(order, receipt = {}) {
  const numberEl = $("#receipt-order-number");
  const metaEl = $("#receipt-order-meta");
  const pdfLink = $("#receipt-pdf-link");
  const thermalLink = $("#receipt-thermal-link");

  if (!numberEl || !metaEl || !pdfLink || !thermalLink || !order?.id) return;

  numberEl.textContent = `${order.order_number} ready`;
  metaEl.textContent = receipt?.email?.queued
    ? `Total ${money(order.grand_total)}. PDF stored, thermal export ready, and email queued to ${receipt.email.address}.`
    : `Total ${money(order.grand_total)}. PDF stored and thermal export ready for download.`;

  pdfLink.href = `/api/orders/${order.id}/receipt`;
  pdfLink.download = `${order.order_number}.pdf`;
  pdfLink.setAttribute("aria-disabled", "false");
  pdfLink.classList.remove("is-disabled");

  thermalLink.href = `/api/orders/${order.id}/receipt?format=thermal`;
  thermalLink.download = `${order.order_number}.escpos`;
  thermalLink.setAttribute("aria-disabled", "false");
  thermalLink.classList.remove("is-disabled");
}

function clearReceiptDock() {
  const numberEl = $("#receipt-order-number");
  const metaEl = $("#receipt-order-meta");
  const pdfLink = $("#receipt-pdf-link");
  const thermalLink = $("#receipt-thermal-link");

  if (numberEl) numberEl.textContent = "No completed sale yet";
  if (metaEl) metaEl.textContent = "Complete a checkout and the PDF receipt download will appear here.";

  [pdfLink, thermalLink].forEach((link) => {
    if (!link) return;
    link.href = "#";
    link.setAttribute("aria-disabled", "true");
    link.classList.add("is-disabled");
  });
}

function syncPrimaryTender(total) {
  const rows = [...$("#tender-lines").children];
  if (rows.length !== 1) return;
  const input = rows[0].querySelector("input");
  if (!input) return;
  const currentValue = Number(input.value || 0);
  if (!input.dataset.manual || currentValue === 0) {
    input.value = total.toFixed(2);
  }
}

function showView(name) {
  $$(".view").forEach((view) => view.classList.remove("active"));
  $(`#view-${name}`)?.classList.add("active");
  $$(".nav-btn").forEach((button) => button.classList.toggle("active", button.dataset.view === name));

  const title = {
    dashboard: "Daily Sales Dashboard",
    records: "Catalogue and Listing",
    tradeins: "Trade-In Intake",
    wantlists: "Customer Wantlists",
    pos: "POS Checkout",
    preorders: "Pre-orders",
    service: "Turntable Service Queue",
    consignment: "Consignment Agreements",
    loyalty: "Loyalty Points",
    search: "Customer Search",
    import: "CSV Import and Export",
    audit: "Audit and Counterfeit Blacklist",
  }[name];

  if ($("#page-title")) $("#page-title").textContent = title || "Vintage Vinyl";
  window.location.hash = name;
  loadView(name);
}

function loadView(name) {
  const actions = {
    dashboard: loadDashboard,
    records: () => loadMeta().then(loadRecords),
    tradeins: async () => {
      await loadMeta();
      if (!$("#trade-rows").children.length) addTradeRow();
    },
    wantlists: async () => {
      await loadMeta();
      await loadWantlists();
    },
    pos: async () => {
      await loadMeta();
      await searchPOS();
    },
    preorders: async () => {
      await loadMeta();
      await loadPreorders();
    },
    service: async () => {
      await loadMeta();
      await loadService();
    },
    consignment: async () => {
      await loadMeta();
      await loadConsignments();
    },
    loyalty: async () => {
      await loadMeta();
      await loadLoyalty();
    },
    search: loadCustomerSearch,
    import: () => {},
    audit: async () => {
      await loadAudit();
      await loadBlacklist();
    },
  };

  const action = actions[name] || (() => {});
  Promise.resolve(action()).catch((error) => toast(error.message, true));
}

$$(".nav-btn").forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.view));
});

function scrollToId(id) {
  $(`#${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function loadHealth() {
  const pill = $("#health-pill");
  if (!pill) return;

  try {
    const data = await api("/api/health");
    pill.textContent = data.ok ? `Healthy - ${data.db}` : "Issue detected";
    pill.classList.toggle("is-ok", Boolean(data.ok));
  } catch (error) {
    pill.textContent = "Connection issue";
    pill.classList.remove("is-ok");
  }
}

async function loadMeta() {
  const data = await api("/api/meta");
  window.meta = data;

  if ($("#dashboard-date")) {
    $("#dashboard-date").textContent = new Date().toLocaleDateString();
  }

  const fill = (selector, items, labelKey = "display_name") => {
    const el = $(selector);
    if (!el) return;
    const oldValue = el.value;
    el.innerHTML = '<option value="">Select...</option>' +
      items.map((item) => `<option value="${item.id}">${esc(item[labelKey] || item.name)}</option>`).join("");
    if ([...el.options].some((option) => option.value === oldValue)) {
      el.value = oldValue;
    }
  };

  fill("#t-customer", data.customers);
  fill("#w-customer", data.customers);
  fill("#pos-customer", data.customers);
  fill("#po-customer", data.customers);
  fill("#s-customer", data.customers);
  fill("#loyal-customer", data.customers);
  fill("#c-consignor", data.consignors, "name");

  if ($("#artist-list")) {
    $("#artist-list").innerHTML = data.artists.map((artist) => `<option value="${esc(artist.name)}">`).join("");
  }
  if ($("#label-list")) {
    $("#label-list").innerHTML = data.labels.map((label) => `<option value="${esc(label.name)}">`).join("");
  }

  await loadPreorderRecords();
  return data;
}

async function loadDashboard() {
  try {
    const data = await api("/api/dashboard");
    const kpis = [
      ["Sales (gross)", money(data.kpi.gross)],
      ["Sales (net)", money(data.kpi.net)],
      ["Transactions", data.kpi.transactions],
      ["Avg basket", money(data.kpi.avg_basket)],
      ["New customers", data.kpi.new_customers],
      ["Wantlist matches", data.kpi.wantlist_matches],
      ["Pre-orders", data.kpi.preorders_received],
    ];

    $("#kpis").innerHTML = kpis
      .map(
        ([label, value]) =>
          `<div class="kpi"><small>${label}</small><strong>${value}</strong><em>live</em></div>`
      )
      .join("");

    const hourly = data.hourly.length ? data.hourly : [...Array(8)].map((_, index) => ({ h: String(index).padStart(2, "0"), total: 0 }));
    const hourlyMax = Math.max(...hourly.map((row) => Number(row.total)), 1);
    $("#hourly-chart").innerHTML = hourly
      .map(
        (row) =>
          `<div class="bar" title="${row.h}:00 ${money(row.total)}"><span style="height:${Math.max(6, Number(row.total) / hourlyMax * 200)}px"></span><small>${row.h}</small></div>`
      )
      .join("");

    const genres = data.genre.length ? data.genre : [{ genre: "No sales", total: 0 }];
    const genreMax = Math.max(...genres.map((row) => Number(row.total)), 1);
    $("#genre-chart").innerHTML = genres
      .map(
        (row) =>
          `<div class="donut-line"><span>${esc(row.genre)}</span><div class="progress"><span style="width:${Math.max(1, Number(row.total) / genreMax * 100)}%"></span></div><strong>${money(row.total)}</strong></div>`
      )
      .join("");

    $("#top-items tbody").innerHTML =
      data.top_items
        .map(
          (row) =>
            `<tr onclick="showRecord(${row.inventory_id})"><td>${esc(row.artist)}</td><td>${esc(row.title)}</td><td>${row.qty}</td><td>${money(row.total)}</td></tr>`
        )
        .join("") || '<tr><td colspan="4" class="muted">No sales today yet.</td></tr>';

    $("#low-stock tbody").innerHTML =
      data.low_stock
        .map(
          (row) =>
            `<tr><td>${esc(row.artist)} - ${esc(row.title)}</td><td>${esc(row.bin_code)}</td><td>${money(row.asking_price)}</td></tr>`
        )
        .join("") || '<tr><td colspan="3" class="muted">No low-stock alerts.</td></tr>';

    $("#payouts tbody").innerHTML =
      data.payouts_due
        .map(
          (row) =>
            `<tr><td>${esc(row.agreement_number)}</td><td>${esc(row.consignor)}</td><td>${esc(row.sale_date)}</td><td>${money(row.payout)}</td></tr>`
        )
        .join("") || '<tr><td colspan="4" class="muted">No payouts due.</td></tr>';
  } catch (error) {
    toast(error.message, true);
  }
}

function defaultRPM() {
  const format = $("#r-format").value;
  const value = format === "7in" || format === "10in" || format === "12in_maxi" ? "45" : "33";
  document.querySelector(`input[name="rpm"][value="${value}"]`).checked = true;
}

function setDecade() {
  const year = Number.parseInt($("#r-year").value || "0", 10);
  $("#r-decade").value = year ? `${Math.floor(year / 10) * 10}s` : "";
}

function gradeDescription() {
  const grade = $("#r-media").value;
  $("#grade-explain").textContent = `${grade}: ${window.meta?.grade_defs?.[grade] || ""}`;
}

async function checkMatrix() {
  const sideA = $("#r-ma").value;
  const sideB = $("#r-mb").value;
  const banner = $("#duplicate-banner");

  if (!sideA && !sideB) {
    banner.classList.add("hidden");
    return;
  }

  try {
    const query = new URLSearchParams({ q: sideA || sideB });
    const data = await api(`/api/records?${query}`);
    const hit = data.items.find(Boolean);
    if (!hit) {
      banner.classList.add("hidden");
      return;
    }

    banner.textContent = `Possible duplicate pressing - ${hit.artist} / ${hit.title}. Save is still allowed.`;
    banner.classList.remove("hidden");
  } catch (error) {
    banner.classList.add("hidden");
  }
}

async function loadRecords() {
  try {
    const params = new URLSearchParams({
      q: $("#catalogue-search").value,
      grade: $("#filter-grade").value,
      country: $("#filter-country").value,
      format: $("#filter-format").value,
      in_stock: $("#filter-stock").checked ? "1" : "",
    });

    const data = await api(`/api/records?${params}`);
    $("#record-list").innerHTML =
      data.items
        .map(
          (record) => `
            <article class="record-card">
              <div class="meta">
                <span>${record.year} - ${esc(record.country_code)} - ${FORMAT_LABELS[record.format] || record.format}</span>
                <span>${record.media_grade || "-"}</span>
              </div>
              <h3>${esc(record.artist)} - ${esc(record.title)}</h3>
              <p>${esc(record.label)} - ${esc(record.catalogue_number)} - ${esc(record.genres || "")}</p>
              <div class="meta">
                <span>${record.stock} in stock</span>
                <strong>${money(record.price)}</strong>
              </div>
              <div class="record-actions">
                <button class="btn secondary" onclick="showRecord(${record.id})">View</button>
                ${record.stock > 0 ? `<button class="btn primary" onclick="addToCart(${record.id})">Add to cart</button>` : ""}
                <button class="btn secondary" onclick="priceAssist(${record.id})">Pricing</button>
              </div>
            </article>
          `
        )
        .join("") || '<div class="rule-callout">No results. Try relaxing the most restrictive filter.</div>';
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveRecord(event) {
  event.preventDefault();
  try {
    const artist = $("#r-artist").value.trim();
    const label = $("#r-label").value.trim();
    const media = $("#r-media").value;
    const hasInnerSleeve = $("#r-inner").checked;
    const photoFiles = $("#r-photos").files;

    if (media === "NM" && !hasInnerSleeve && !window.confirm("NM without the original inner sleeve will be downgraded to VG+. Continue?")) {
      return;
    }
    if (photoFiles.length > 6) throw new Error("Up to 6 cover photos are allowed");

    const photoPaths = await uploadFiles(photoFiles);
    await api("/api/records", {
      method: "POST",
      body: JSON.stringify({
        artist_name: artist,
        title: $("#r-title").value,
        label_name: label,
        create_artist: true,
        create_label: true,
        catalogue_number: $("#r-cat").value,
        country_code: $("#r-country").value,
        year: Number($("#r-year").value),
        format: $("#r-format").value,
        rpm: Number(document.querySelector('input[name="rpm"]:checked').value),
        mono_stereo: $("#r-mono").value,
        matrix_runout_a: $("#r-ma").value,
        matrix_runout_b: $("#r-mb").value,
        media_grade: media,
        sleeve_grade: $("#r-sleeve").value,
        has_original_inner_sleeve: hasInnerSleeve,
        asking_price: $("#r-price").value,
        bin_code: $("#r-bin").value,
        negotiable: $("#r-neg").checked,
        genres: $("#r-genres").value.split(",").map((value) => value.trim()).filter(Boolean),
        inserts: $("#r-inserts").value.split(";").map((value) => value.trim()).filter(Boolean),
        photo_paths: photoPaths,
        notes: $("#r-notes").value,
      }),
    });

    toast("Listing saved successfully");
    $("#record-form").reset();
    setDecade();
    defaultRPM();
    gradeDescription();
    await loadRecords();
    await loadDashboard();
  } catch (error) {
    toast(error.message, true);
  }
}

async function priceAssist(id) {
  try {
    const data = await api(`/api/records/${id}/pricing`);
    toast(`Pricing range ${money(data.low)} to ${money(data.high)} - ${data.confidence}`);
  } catch (error) {
    toast(error.message, true);
  }
}

async function showRecord(id) {
  try {
    const data = await api(`/api/records/${id}`);
    const record = data.record;
    window.alert(
      `${record.artist} - ${record.title}\n${record.year} - ${FORMAT_LABELS[record.format] || record.format}\nInventory: ${data.inventory.length}\nGenres: ${record.genres || "-"}`
    );
  } catch (error) {
    toast(error.message, true);
  }
}

function addTradeRow() {
  tradeRows += 1;
  const row = document.createElement("div");
  row.className = "line-row";
  row.dataset.idx = String(tradeRows);
  row.innerHTML = `
    <div><label class="muted">Pressing ID<input class="tr-pressing" type="number" required></label></div>
    <div><label class="muted">Media<select class="tr-media"><option>M</option><option>NM</option><option>VG+</option><option selected>VG</option><option>G+</option><option>G</option><option>F</option><option>P</option></select></label></div>
    <div><label class="muted">Sleeve<select class="tr-sleeve"><option>M</option><option>NM</option><option>VG+</option><option selected>VG</option><option>G+</option><option>G</option><option>F</option><option>P</option></select></label></div>
    <div><label class="muted">Condition Photos<input class="tr-photos-file" type="file" accept="image/jpeg,image/png" multiple required></label></div>
    <button type="button" class="btn secondary" onclick="this.closest('.line-row').remove()">Remove</button>
  `;
  $("#trade-rows").appendChild(row);
}

async function uploadFiles(fileList) {
  const files = [...fileList];
  if (files.length > 6) throw new Error("Maximum 6 images per listing");
  if (files.some((file) => file.size > 8 * 1024 * 1024)) throw new Error("Each image must be 8 MB or smaller");
  if (!files.length) return [];

  const form = new FormData();
  files.forEach((file) => form.append("files", file));

  const response = await fetch("/api/upload", { method: "POST", body: form });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.error || data.message || "Upload failed");
  }
  return data.paths;
}

async function saveTradeIn(event) {
  event.preventDefault();
  try {
    const rows = [];
    for (const row of [...$("#trade-rows").children]) {
      const files = row.querySelector(".tr-photos-file").files;
      if (!files.length) throw new Error("Each trade-in row requires at least one condition photo");
      const photos = await uploadFiles(files);
      rows.push({
        pressing_id: Number(row.querySelector(".tr-pressing").value),
        media_grade: row.querySelector(".tr-media").value,
        sleeve_grade: row.querySelector(".tr-sleeve").value,
        photos,
      });
    }

    if (!rows.length) throw new Error("Add at least one record");

    const data = await api("/api/trade-ins", {
      method: "POST",
      body: JSON.stringify({
        customer_id: Number($("#t-customer").value) || null,
        offer_mode: document.querySelector('input[name="offer_mode"]:checked').value,
        id_type: $("#t-idtype").value,
        id_number: $("#t-idnum").value,
        signature: $("#t-sign").value,
        customer_accepts: $("#t-accept").checked,
        rows,
        notes: $("#t-notes").value,
      }),
    });

    $("#trade-total").textContent = money(data.offer_total);
    toast(`Trade-in saved - offer ${money(data.offer_total)}`);
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadWantlists() {
  try {
    const data = await api("/api/wantlists");
    $("#wantlist-table tbody").innerHTML = data.items
      .map(
        (item) =>
          `<tr><td>${esc(item.display_name)}</td><td>${esc(item.artist_query || "")}</td><td>${esc(item.title_query || "")}</td><td>${item.min_media_grade}</td><td>${item.max_price ? money(item.max_price) : "-"}</td><td>${item.is_active ? "Yes" : "No"}</td><td>${item.priority}</td></tr>`
      )
      .join("");
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveWantlist(event) {
  event.preventDefault();
  try {
    await api("/api/wantlists", {
      method: "POST",
      body: JSON.stringify({
        customer_id: Number($("#w-customer").value),
        artist: $("#w-artist").value,
        title: $("#w-title").value,
        label: $("#w-label").value,
        catalogue: $("#w-cat").value,
        year_from: $("#w-yf").value || null,
        year_to: $("#w-yt").value || null,
        max_price: $("#w-price").value || null,
        min_media_grade: $("#w-grade").value,
        notify_email: $("#w-email").checked,
        notify_sms: $("#w-sms").checked,
        notify_push: $("#w-push").checked,
        priority: Number($("#w-priority").value),
        active: true,
        notes: $("#w-notes").value,
      }),
    });

    toast("Wantlist created");
    await loadWantlists();
  } catch (error) {
    toast(error.message, true);
  }
}

async function searchPOS() {
  try {
    const data = await api(`/api/records?q=${encodeURIComponent($("#pos-search").value || "")}`);
    $("#pos-results").innerHTML =
      data.items
        .filter((item) => item.stock > 0)
        .slice(0, 12)
        .map(
          (record) => `
            <article class="record-card">
              <h3>${esc(record.artist)} - ${esc(record.title)}</h3>
              <p>${record.year} - ${record.media_grade || "-"}</p>
              <div class="meta">
                <strong>${money(record.price)}</strong>
                <button class="btn primary" onclick="addFirstInventory(${record.id})">Add</button>
              </div>
            </article>
          `
        )
        .join("") || '<div class="rule-callout">No available inventory found.</div>';
  } catch (error) {
    toast(error.message, true);
  }
}

async function addFirstInventory(recordId) {
  try {
    const data = await api(`/api/records/${recordId}`);
    const inventory = data.inventory.find((item) => ["in_stock", "reserved", "consignment"].includes(item.status));
    if (!inventory) throw new Error("No available copy");
    if (cart.some((item) => item.inventory_id === inventory.id)) {
      toast("Item already in cart");
      return;
    }

    cart.push({
      inventory_id: inventory.id,
      title: `${data.record.artist} - ${data.record.title}`,
      price: Number(inventory.asking_price),
      media_grade: inventory.media_grade,
    });

    renderCart();
  } catch (error) {
    toast(error.message, true);
  }
}

function addToCart(recordId) {
  addFirstInventory(recordId);
}

function removeCartLine(index) {
  cart.splice(index, 1);
  renderCart();
}

function renderCart() {
  const countEl = $("#cart-count");
  const linesEl = $("#cart-lines");
  const totalEl = $("#cart-total");

  if (countEl) countEl.textContent = `${cart.length} items`;
  if (linesEl) {
    linesEl.innerHTML =
      cart
        .map(
          (item, index) => `
            <div class="line-row">
              <div>
                <strong>${esc(item.title)}</strong>
                <div class="muted">${esc(item.media_grade)}</div>
              </div>
              <div>${money(item.price)}</div>
              <div><input type="number" min="0" max="100" value="${item.discount || 0}" onchange="cart[${index}].discount=Number(this.value)||0;renderCart()"></div>
              <div class="muted">Line discount %</div>
              <button class="btn secondary" type="button" onclick="removeCartLine(${index})">Remove</button>
            </div>
          `
        )
        .join("") || '<div class="rule-callout">Cart is empty.</div>';
  }

  const subtotal = cart.reduce((sum, item) => sum + item.price * (1 - (item.discount || 0) / 100), 0);
  const orderDiscount = Number($("#pos-discount")?.value || 0);
  const shipping = Number($("#pos-shipping")?.value || 0);
  const total = subtotal * (1 - orderDiscount / 100) + shipping;

  if (totalEl) totalEl.textContent = money(total);
  syncPrimaryTender(total);
}

function addTender() {
  const row = document.createElement("div");
  row.className = "tender-row";
  row.innerHTML = `
    <select>
      <option>cash</option>
      <option>card</option>
      <option>voucher</option>
      <option>store_credit</option>
    </select>
    <input type="number" step="0.01" value="0.00" oninput="this.dataset.manual='true'">
    <button type="button" class="btn secondary" onclick="this.parentElement.remove(); renderCart()">Remove</button>
  `;
  $("#tender-lines").appendChild(row);
  renderCart();
}

function collectTenders() {
  return [...$("#tender-lines").children].map((row) => {
    const type = row.querySelector("select").value;
    return {
      type,
      amount: row.querySelector("input").value,
      card_token: type === "card" ? `tok_demo_${Date.now()}` : undefined,
      voucher_code: type === "voucher" ? "DEMO" : undefined,
      store_credit_txn: type === "store_credit" ? `SC_${Date.now()}` : undefined,
    };
  });
}

async function checkout() {
  try {
    if (!cart.length) throw new Error("Cart is empty");

    const data = await api("/api/pos/checkout", {
      method: "POST",
      body: JSON.stringify({
        customer_id: Number($("#pos-customer").value) || null,
        items: cart.map((item) => ({
          inventory_id: item.inventory_id,
          qty: 1,
          line_discount_pct: item.discount || 0,
        })),
        order_discount_pct: Number($("#pos-discount").value),
        shipping_total: $("#pos-shipping").value,
        tenders: collectTenders(),
        email_receipt: $("#pos-email").checked,
      }),
    });

    setReceiptDock(data.order, data.receipt);
    downloadFile(`/api/orders/${data.order.id}/receipt`, `${data.order.order_number}.pdf`);
    toast(`Paid ${data.order.order_number} - ${money(data.order.grand_total)}. Receipt download started.`);

    cart = [];
    $("#tender-lines").innerHTML = "";
    addTender();
    renderCart();
    await searchPOS();
    await loadDashboard();
    await loadHealth();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadPreorderRecords() {
  try {
    const data = await api("/api/records");
    const preorders = data.items.filter((item) => item.pre_order);
    $("#po-record").innerHTML =
      preorders.map((record) => `<option value="${record.id}">${esc(record.artist)} - ${esc(record.title)} - ${record.year}</option>`).join("") ||
      '<option value="">No active pre-orders</option>';
  } catch (error) {
    // This list is supplementary, so keep the UI responsive if the call fails.
  }
}

async function loadPreorders() {
  try {
    await loadPreorderRecords();
    const data = await api("/api/records");
    $("#release-list").innerHTML =
      data.items
        .filter((item) => item.pre_order)
        .map(
          (item) =>
            `<div class="timeline-item"><strong>${esc(item.title)}</strong><div class="muted">Release ${item.release_date || "-"}</div></div>`
        )
        .join("") || '<div class="rule-callout">No active pre-orders.</div>';
  } catch (error) {
    toast(error.message, true);
  }
}

async function savePreorder(event) {
  event.preventDefault();
  try {
    const data = await api("/api/preorders", {
      method: "POST",
      body: JSON.stringify({
        record_id: Number($("#po-record").value),
        customer_id: Number($("#po-customer").value),
        quantity: Number($("#po-qty").value),
        deposit_amount: $("#po-deposit").value,
        deposit_tender: $("#po-tender").value,
        ship_address: $("#po-address").value,
        notes: $("#po-notes").value,
      }),
    });
    toast(`Pre-order created - release ${data.release_date}`);
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadService() {
  try {
    const data = await api("/api/service-tickets");
    $("#service-list").innerHTML =
      data.items
        .map(
          (item) => `
            <div class="timeline-item">
              <div class="panel-head">
                <strong>${esc(item.ticket_number)} - ${esc(item.brand)} ${esc(item.model)}</strong>
                <span class="pill">${item.status}</span>
              </div>
              <div class="muted">Quote ${money(item.current_quote)} / limit ${money(item.authorised_limit)} - contacts ${item.contact_attempts}</div>
              <div class="record-actions" style="margin-top: 10px;">
                <button class="btn secondary" onclick="serviceAction(${item.id}, 'diagnosing')">Start diagnosis</button>
                <button class="btn secondary" onclick="serviceAction(${item.id}, 'ready')">Ready</button>
                <button class="btn secondary" onclick="contactTicket(${item.id})">Log contact</button>
              </div>
            </div>
          `
        )
        .join("") || '<div class="rule-callout">No service tickets.</div>';
  } catch (error) {
    toast(error.message, true);
  }
}

async function createTicket(event) {
  event.preventDefault();
  try {
    const cosmeticDamage = $("#sc-cosmetic").checked;
    const photoFiles = $("#s-photos").files;
    if (cosmeticDamage && !photoFiles.length) {
      throw new Error("Cosmetic damage requires at least one photo");
    }

    const photos = await uploadFiles(photoFiles);
    const data = await api("/api/service-tickets", {
      method: "POST",
      body: JSON.stringify({
        customer_id: Number($("#s-customer").value),
        equipment_type: $("#s-equipment").value,
        brand: $("#s-brand").value,
        model: $("#s-model").value,
        serial_number: $("#s-serial").value,
        authorised_limit: $("#s-limit").value,
        symptoms: $("#s-symptoms").value,
        photos,
        checklist: {
          powers_on: $("#sc-power").checked,
          platter_spins: $("#sc-platter").checked,
          arm_balanced: $("#sc-arm").checked,
          stylus_inspected: $("#sc-stylus").checked,
          cosmetic_damage_noted: cosmeticDamage,
        },
        notes: $("#s-notes").value,
      }),
    });

    toast(`Created ${data.ticket_number}`);
    await loadService();
  } catch (error) {
    toast(error.message, true);
  }
}

async function serviceAction(id, status) {
  if (!window.confirm(`Change ticket status to ${status}?`)) return;
  try {
    await api(`/api/service-tickets/${id}/status`, {
      method: "POST",
      body: JSON.stringify({ status }),
    });
    await loadService();
  } catch (error) {
    toast(error.message, true);
  }
}

async function contactTicket(id) {
  try {
    await api(`/api/service-tickets/${id}/contact`, { method: "POST" });
    toast("Contact attempt logged");
    await loadService();
  } catch (error) {
    toast(error.message, true);
  }
}

function addTier() {
  const row = document.createElement("div");
  row.className = "tier-row";
  row.innerHTML = '<input placeholder="From"><input placeholder="To"><input placeholder="%">';
  $("#tier-editor").appendChild(row);
}

async function loadConsignments() {
  try {
    const data = await api("/api/consignments");
    $("#cons-table tbody").innerHTML = data.items
      .map(
        (item) =>
          `<tr><td>${esc(item.agreement_number)}</td><td>${esc(item.consignor)}</td><td>${item.default_payout_pct}%</td><td>${item.statement_frequency}</td><td>${item.finalised_at ? "Finalised" : "Draft"}</td></tr>`
      )
      .join("");
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveConsignment(event) {
  event.preventDefault();
  try {
    const tiers = [...$("#tier-editor").children]
      .map((row) => {
        const inputs = row.querySelectorAll("input");
        return {
          from_days: Number(inputs[0].value),
          to_days: inputs[1].value ? Number(inputs[1].value) : null,
          pct: Number(inputs[2].value),
        };
      })
      .filter((tier) => tier.from_days !== 0 || tier.to_days || tier.pct);

    const data = await api("/api/consignments", {
      method: "POST",
      body: JSON.stringify({
        consignor_id: Number($("#c-consignor").value),
        effective_date: $("#c-date").value || new Date().toISOString().slice(0, 10),
        default_payout_pct: Number($("#c-payout").value),
        auto_return_days: Number($("#c-return").value),
        statement_frequency: $("#c-freq").value,
        sale_floor: $("#c-floor").checked,
        signature: $("#c-sign").value,
        tiers,
        notes: $("#c-notes").value,
      }),
    });

    toast(`Agreement ${data.agreement_number} finalised`);
    await loadConsignments();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadLoyalty() {
  const customerId = Number($("#loyal-customer").value) || 1;
  try {
    const data = await api(`/api/loyalty/${customerId}`);
    $("#loyalty-card").innerHTML = `
      <div class="kpi"><small>Current points</small><strong>${data.balance}</strong><em>${money(data.monetary_equivalent)}</em></div>
      <div class="kpi"><small>Tier</small><strong>${esc(data.customer?.tier || "basic")}</strong><em>${data.balance % 1000} progress</em></div>
      <div class="kpi"><small>Expiring in 90 days</small><strong>${data.expiring_90_days}</strong><em>watch list</em></div>
    `;
    $("#loyal-table tbody").innerHTML =
      data.transactions
        .map(
          (item) =>
            `<tr><td>${esc(item.created_at)}</td><td>${esc(item.source)}</td><td class="${item.delta_points >= 0 ? "high" : "danger"}">${item.delta_points}</td><td>${esc(item.note || "")}</td></tr>`
        )
        .join("") || '<tr><td colspan="4">No ledger entries.</td></tr>';
    $("#loyal-export").href = `/api/loyalty/${customerId}/export`;
  } catch (error) {
    toast(error.message, true);
  }
}

async function redeemPoints(event) {
  event.preventDefault();
  try {
    const customerId = Number($("#loyal-customer").value) || 1;
    const points = Number($("#redeem-points").value);
    if (!window.confirm(`Redeem ${points} points?`)) return;

    const data = await api(`/api/loyalty/${customerId}/redeem`, {
      method: "POST",
      body: JSON.stringify({
        points,
        reward: $("#redeem-reward").value,
      }),
    });

    toast(`Redeemed - balance ${data.balance}`);
    await loadLoyalty();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadCustomerSearch() {
  try {
    const params = new URLSearchParams({
      q: $("#search-input")?.value || "",
      grade: $("#s-grade")?.value || "",
      format: $("#s-format")?.value || "",
      country: $("#s-country")?.value || "",
      min_price: $("#s-min")?.value || "",
      max_price: $("#s-max")?.value || "",
      in_stock: $("#s-stock")?.checked ? "1" : "",
    });

    const data = await api(`/api/records?${params}`);
    const state = { view: "search", ...Object.fromEntries(params.entries()) };
    history.replaceState(null, "", `?${new URLSearchParams(state)}`);

    $("#search-results").innerHTML =
      data.items
        .map(
          (record) => `
            <article class="record-card">
              <div class="meta">
                <span>${record.year} - ${esc(record.country_code)}</span>
                <span>${esc(record.media_grade || "-")}</span>
              </div>
              <h3>${esc(record.artist)} - ${esc(record.title)}</h3>
              <p>${esc(record.label)} - ${esc(record.catalogue_number)}</p>
              <div class="meta">
                <strong>${money(record.price)}</strong>
                <span>${record.stock} stock</span>
              </div>
              <div class="record-actions">
                <button class="btn secondary" onclick="showRecord(${record.id})">View</button>
                <button class="btn secondary" onclick='addWantFromResult(${jsq(record.artist)}, ${jsq(record.title)})'>Wantlist</button>
                ${record.stock ? `<button class="btn primary" onclick="addToCart(${record.id})">Add to cart</button>` : ""}
              </div>
            </article>
          `
        )
        .join("") || '<div class="rule-callout">No results. Consider relaxing grade, country, or price.</div>';
  } catch (error) {
    toast(error.message, true);
  }
}

function addWantFromResult(artist, title) {
  showView("wantlists");
  $("#w-artist").value = artist;
  $("#w-title").value = title;
  toast("Wantlist form prefilled");
}

async function importCsv(event) {
  event.preventDefault();
  const file = $("#csv-file").files[0];
  if (!file) return;

  const form = new FormData();
  form.append("file", file);
  form.append("dry_run", $("#dry-run").checked);

  try {
    const response = await fetch("/api/import", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.message || "Import failed");

    $("#import-report").innerHTML = `
      <div class="rule-callout">
        <strong>Total:</strong> ${data.total}
        <strong>Imported:</strong> ${data.imported}
        <strong>Errors:</strong> ${data.errors}
        <pre>${esc(JSON.stringify(data.error_breakdown, null, 2))}</pre>
      </div>
    `;
    toast("Import completed");
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadBlacklist() {
  try {
    const data = await api("/api/blacklist");
    $("#blacklist-table tbody").innerHTML = data.items
      .map(
        (item) =>
          `<tr><td>${esc(item.matrix_runout_a)} / ${esc(item.matrix_runout_b || "")}</td><td>${esc(item.title || "")}</td><td>${esc(item.reason)}</td><td>${esc(item.added_at)}</td></tr>`
      )
      .join("");
  } catch (error) {
    toast(error.message, true);
  }
}

async function addBlacklist(event) {
  event.preventDefault();
  try {
    await api("/api/blacklist", {
      method: "POST",
      body: JSON.stringify({
        matrix_a: $("#b-a").value,
        matrix_b: $("#b-b").value,
        artist_name: $("#b-artist").value,
        title: $("#b-title").value,
        reason: $("#b-reason").value,
        source_authority: $("#b-source").value,
      }),
    });

    toast("Blacklist entry added");
    event.target.reset();
    await loadBlacklist();
  } catch (error) {
    toast(error.message, true);
  }
}

async function loadAudit() {
  try {
    const data = await api("/api/audit");
    $("#audit-table tbody").innerHTML = data.items
      .map(
        (item) =>
          `<tr><td>${esc(item.created_at)}</td><td>${esc(item.entity_type)} #${item.entity_id || ""}</td><td>${esc(item.action)}</td><td>${esc(item.actor || "")}</td><td><code>${esc(item.context || "")}</code></td></tr>`
      )
      .join("");
  } catch (error) {
    toast(error.message, true);
  }
}

const catalogueSearchDebounced = debounced(loadRecords, 250);
const customerSearchDebounced = debounced(loadCustomerSearch, 250);

function handleCatalogueSearch() {
  catalogueSearchDebounced();
}

function handleCustomerSearch() {
  customerSearchDebounced();
}

function socketioClientFallback() {
  try {
    if (window.io) {
      const socket = window.io();
      socket.on("dashboard_update", () => {
        loadDashboard();
        loadHealth();
      });
      socket.on("inventory_listed", () => {
        loadDashboard();
        if (window.location.hash === "#records" || !window.location.hash) loadRecords();
      });
      return;
    }
  } catch (error) {
    // Fall back to polling below.
  }

  window.setInterval(() => {
    if (window.location.hash === "#dashboard" || !window.location.hash) {
      loadDashboard();
      loadHealth();
    }
  }, 20000);
}

function init() {
  clearReceiptDock();
  addTender();
  defaultRPM();
  setDecade();
  gradeDescription();
  loadHealth();

  $("#pos-discount")?.addEventListener("input", renderCart);
  $("#pos-shipping")?.addEventListener("input", renderCart);
  $("#r-media")?.addEventListener("change", gradeDescription);

  const initialView = window.location.hash.replace("#", "") || "dashboard";
  showView(initialView);
  loadMeta().catch((error) => toast(error.message, true));

  window.setInterval(() => {
    fetch("/api/reservations/release", { method: "POST" }).catch(() => {});
  }, 60000);

  window.setInterval(loadHealth, 30000);
  socketioClientFallback();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
