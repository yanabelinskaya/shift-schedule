(() => {
  const root = document.querySelector("[data-manager-employees]");
  if (!root) {
    return;
  }

  const section = document.querySelector("[data-employee-section]") || root;
  const tableBody = section.querySelector("[data-employee-rows]");
  const rows = tableBody
    ? Array.from(tableBody.querySelectorAll("tr[data-employee-row]"))
    : [];
  const emptyRow = tableBody ? tableBody.querySelector("[data-empty-row]") : null;
  const searchInput = section.querySelector("[data-employee-search]");
  const positionFilter = section.querySelector("[data-employee-position-filter]");
  const statusFilter = section.querySelector("[data-employee-status-filter]");
  const countLabel = section.querySelector("[data-employee-count]");
  const detail = section.querySelector("[data-employee-detail]");
  const detailName = detail ? detail.querySelector("[data-detail-name]") : null;
  const detailPosition = detail ? detail.querySelector("[data-detail-position]") : null;
  const detailContacts = detail ? detail.querySelector("[data-detail-contacts]") : null;
  const detailRate = detail ? detail.querySelector("[data-detail-rate]") : null;
  const detailLimit = detail ? detail.querySelector("[data-detail-limit]") : null;
  const detailStatus = detail ? detail.querySelector("[data-detail-status]") : null;
  const detailMessage = detail ? detail.querySelector("[data-detail-message]") : null;
  const detailActions = detail ? Array.from(detail.querySelectorAll("[data-detail-action]")) : [];

  const normalize = (value) =>
    String(value || "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .trim();

  const statusMap = {
    active: { label: "Активен", className: "success" },
    vacation: { label: "В отпуске", className: "warning" },
    sick: { label: "Больничный", className: "danger" },
    inactive: { label: "Неактивен", className: "muted" },
  };

  let selectedRow = null;

  const showMessage = (text, type) => {
    if (!detailMessage) {
      return;
    }
    detailMessage.textContent = text || "";
    detailMessage.classList.remove("is-error", "is-success", "is-warning");
    if (!text) {
      detailMessage.hidden = true;
      return;
    }
    detailMessage.hidden = false;
    if (type === "error") {
      detailMessage.classList.add("is-error");
    } else if (type === "warning") {
      detailMessage.classList.add("is-warning");
    } else {
      detailMessage.classList.add("is-success");
    }
  };

  const updateCountLabel = (visibleCount) => {
    if (!countLabel) {
      return;
    }
    countLabel.textContent = `Показано: ${visibleCount} из ${rows.length}`;
  };

  const updateRowStatus = (row, status) => {
    if (!row) {
      return;
    }
    const statusInfo = statusMap[status] || statusMap.active;
    const pill = row.querySelector("[data-status-pill]");
    row.dataset.status = status;
    row.classList.toggle("is-inactive", status === "inactive");
    if (pill) {
      pill.textContent = statusInfo.label;
      pill.className = "status-pill";
      if (statusInfo.className) {
        pill.classList.add(statusInfo.className);
      }
    }
  };

  const updateDetail = (row) => {
    if (!row || !detail) {
      return;
    }
    const name = row.dataset.name || "—";
    const position = row.dataset.position || "—";
    const phone = row.dataset.phone || "—";
    const email = row.dataset.email || "—";
    const rate = row.dataset.rate || "—";
    const limit = row.dataset.limit || "—";
    const statusInfo = statusMap[row.dataset.status] || statusMap.active;

    if (detailName) {
      detailName.textContent = row.querySelector("td")?.textContent || name;
    }
    if (detailPosition) {
      detailPosition.textContent = position === "—" ? position : row.querySelector("td:nth-child(2)")?.textContent || position;
    }
    if (detailContacts) {
      const phoneValue = phone === "—" ? "" : phone;
      const emailValue = email === "—" ? "" : email;
      if (!phoneValue && !emailValue) {
        detailContacts.textContent = "—";
      } else if (!phoneValue) {
        detailContacts.textContent = emailValue;
      } else if (!emailValue) {
        detailContacts.textContent = phoneValue;
      } else {
        detailContacts.textContent = `${phoneValue} · ${emailValue}`;
      }
    }
    if (detailRate) {
      detailRate.textContent = rate;
    }
    if (detailLimit) {
      detailLimit.textContent = limit === "—" ? "—" : `${limit} ч`;
    }
    if (detailStatus) {
      detailStatus.textContent = statusInfo.label;
    }
  };

  const selectRow = (row) => {
    if (!row) {
      return;
    }
    rows.forEach((item) => item.classList.remove("is-selected"));
    row.classList.add("is-selected");
    selectedRow = row;
    updateDetail(row);
  };

  const getFilterValue = (input) => normalize(input ? input.value : "");

  const applyFilters = () => {
    const searchValue = getFilterValue(searchInput);
    const positionValue = getFilterValue(positionFilter);
    const statusValue = getFilterValue(statusFilter);
    let visibleCount = 0;

    rows.forEach((row) => {
      const name = normalize(row.dataset.name || "");
      const position = normalize(row.dataset.position || "");
      const phone = normalize(row.dataset.phone || "");
      const email = normalize(row.dataset.email || "");
      const status = normalize(row.dataset.status || "");

      const matchesSearch =
        !searchValue ||
        name.includes(searchValue) ||
        position.includes(searchValue) ||
        phone.includes(searchValue) ||
        email.includes(searchValue);
      const matchesPosition = !positionValue || position === positionValue;
      const matchesStatus = !statusValue || status === statusValue;

      const shouldShow = matchesSearch && matchesPosition && matchesStatus;
      row.hidden = !shouldShow;
      row.style.display = shouldShow ? "" : "none";
      if (shouldShow) {
        visibleCount += 1;
      }
    });

    if (emptyRow) {
      emptyRow.hidden = visibleCount > 0;
    }
    updateCountLabel(visibleCount);

    if (selectedRow && (selectedRow.hidden || selectedRow.style.display === "none")) {
      selectedRow.classList.remove("is-selected");
      selectedRow = null;
    }
    if (!selectedRow) {
      const firstVisible = rows.find((row) => !row.hidden);
      if (firstVisible) {
        selectRow(firstVisible);
      }
    }
  };

  rows.forEach((row) => {
    updateRowStatus(row, row.dataset.status || "active");
    row.addEventListener("click", (event) => {
      if (event.target.closest("button")) {
        return;
      }
      selectRow(row);
    });
    const openButton = row.querySelector("[data-action='select-employee']");
    if (openButton) {
      openButton.addEventListener("click", () => {
        selectRow(row);
      });
    }
  });

  detailActions.forEach((button) => {
    button.addEventListener("click", () => {
      if (!selectedRow) {
        showMessage("Выберите сотрудника.", "warning");
        return;
      }
      const status = button.dataset.detailAction;
      if (!status) {
        return;
      }
      updateRowStatus(selectedRow, status);
      updateDetail(selectedRow);
      applyFilters();
      showMessage("Статус сотрудника обновлен.", "success");
    });
  });

  if (searchInput) {
    searc