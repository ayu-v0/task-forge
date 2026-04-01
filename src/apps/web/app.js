const batchGrid = document.getElementById("batch-grid");
const statusText = document.getElementById("status-text");
const searchInput = document.getElementById("search-input");
const statusSelect = document.getElementById("status-select");
const sortSelect = document.getElementById("sort-select");
const refreshButton = document.getElementById("refresh-button");

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function renderEmpty(message) {
  batchGrid.innerHTML = `<article class="empty-state">${message}</article>`;
}

function renderBatches(items) {
  if (!items.length) {
    renderEmpty("No batches matched the current filters.");
    return;
  }

  batchGrid.innerHTML = items
    .map(
      (item) => `
        <article class="batch-card">
          <div class="card-top">
            <div>
              <h2>${item.title}</h2>
              <p class="batch-id">${item.batch_id}</p>
            </div>
            <span class="status-badge status-${item.derived_status}">${item.derived_status}</span>
          </div>
          <dl class="metrics">
            <div><dt>Total tasks</dt><dd>${item.total_tasks}</dd></div>
            <div><dt>Success rate</dt><dd>${item.success_rate}%</dd></div>
            <div><dt>Completed</dt><dd>${item.completed_count}</dd></div>
            <div><dt>Success</dt><dd>${item.success_count}</dd></div>
            <div><dt>Failed</dt><dd>${item.failed_count}</dd></div>
            <div><dt>Cancelled</dt><dd>${item.cancelled_count}</dd></div>
          </dl>
          <div class="timestamps">
            <p><strong>Created:</strong> ${formatDate(item.created_at)}</p>
            <p><strong>Updated:</strong> ${formatDate(item.updated_at)}</p>
          </div>
        </article>
      `,
    )
    .join("");
}

async function loadBatches() {
  const params = new URLSearchParams();
  if (searchInput.value.trim()) {
    params.set("search", searchInput.value.trim());
  }
  if (statusSelect.value) {
    params.set("status", statusSelect.value);
  }
  params.set("sort", sortSelect.value);

  statusText.textContent = "Loading batches...";
  try {
    const response = await fetch(`/task-batches?${params.toString()}`);
    if (!response.ok) {
      throw new Error(`Request failed with status ${response.status}`);
    }
    const payload = await response.json();
    statusText.textContent = `${payload.items.length} batch${payload.items.length === 1 ? "" : "es"} shown`;
    renderBatches(payload.items);
  } catch (error) {
    statusText.textContent = "Unable to load batches.";
    renderEmpty(error.message);
  }
}

searchInput.addEventListener("input", () => {
  loadBatches();
});
statusSelect.addEventListener("change", () => {
  loadBatches();
});
sortSelect.addEventListener("change", () => {
  loadBatches();
});
refreshButton.addEventListener("click", () => {
  loadBatches();
});

loadBatches();
