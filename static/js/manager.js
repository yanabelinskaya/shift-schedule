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
  const statusTabs = Array.from(section.querySelectorAll("[data-status-tab]"));
  const resetButton = section.querySelector("[data-employee-reset]");

  let statusTabValue = "all";

  const normalize = (value) =>
    String(value || "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .trim();

  const updateCountLabel = (visibleCount) => {
    if (!countLabel) {
      return;
    }
    countLabel.textContent = `Показано: ${visibleCount} из ${rows.length}`;
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
      const matchesTab =
        statusTabValue === "all" ||
        (statusTabValue === "active" && status !== "inactive") ||
        (statusTabValue === "inactive" && status === "inactive");

      const shouldShow = matchesSearch && matchesPosition && matchesStatus && matchesTab;
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
  };

  if (searchInput) {
    searchInput.addEventListener("input", () => {
      applyFilters();
    });
  }

  if (positionFilter) {
    positionFilter.addEventListener("change", () => {
      applyFilters();
    });
  }

  if (statusFilter) {
    statusFilter.addEventListener("change", () => {
      applyFilters();
    });
  }

  statusTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      statusTabs.forEach((btn) => btn.classList.remove("active"));
      tab.classList.add("active");
      statusTabValue = tab.dataset.statusTab || "all";
      applyFilters();
    });
  });

  if (resetButton) {
    resetButton.addEventListener("click", () => {
      if (searchInput) {
        searchInput.value = "";
        searchInput.dispatchEvent(new Event("input", { bubbles: true }));
      }
      if (positionFilter) {
        positionFilter.value = "";
        positionFilter.dispatchEvent(new Event("change", { bubbles: true }));
      }
      if (statusFilter) {
        statusFilter.value = "";
        statusFilter.dispatchEvent(new Event("change", { bubbles: true }));
      }
      if (statusTabs.length) {
        statusTabs.forEach((btn) => btn.classList.remove("active"));
        statusTabs[0].classList.add("active");
        statusTabValue = statusTabs[0].dataset.statusTab || "all";
      } else {
        statusTabValue = "all";
      }
      applyFilters();
    });
  }

  applyFilters();
})();

(() => {
  const root = document.querySelector("[data-manager-employee-detail]");
  if (!root) {
    return;
  }

  const absenceUrl = root.dataset.absenceUrl || "";
  const isActive = root.dataset.employeeActive !== "false";
  const statusPill = root.querySelector("[data-employee-status-pill]");
  const statusLabel = root.querySelector("[data-employee-status-label]");

  const typeTabs = Array.from(root.querySelectorAll("[data-absence-type]"));
  const absenceRange = root.querySelector("[data-absence-range]");
  const vacationBalance = root.querySelector("[data-vacation-balance]");
  const vacationBalanceValue = root.querySelector("[data-vacation-balance-value]");
  const absenceHint = root.querySelector("[data-absence-hint]");
  const calendarTitle = root.querySelector("[data-calendar-title]");
  const calendarGrid = root.querySelector("[data-calendar-grid]");
  const calendarPrev = root.querySelector("[data-calendar-prev]");
  const calendarNext = root.querySelector("[data-calendar-next]");
  const absenceMessage = root.querySelector("[data-absence-message]");
  const absenceSave = root.querySelector("[data-absence-save]");
  const absenceReset = root.querySelector("[data-absence-reset]");
  const absenceList = root.querySelector("[data-absence-list]");

  const statusMap = {
    active: { label: "Активен", className: "success" },
    vacation: { label: "В отпуске", className: "warning" },
    sick: { label: "Больничный", className: "danger" },
    inactive: { label: "Неактивен", className: "muted" },
  };

  let selectedAbsences = [];
  const initialTab = typeTabs.find((tab) => tab.classList.contains("active"));
  let currentAbsenceType = initialTab?.dataset.absenceType || "vacation";
  let rangeStart = null;
  let rangeEnd = null;
  let calendarMonth = new Date();

  const pad = (value) => String(value).padStart(2, "0");

  const toDateOnly = (value) =>
    new Date(value.getFullYear(), value.getMonth(), value.getDate());

  const toISODate = (value) =>
    `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`;

  const parseISODate = (value) => {
    if (!value) {
      return null;
    }
    const [year, month, day] = String(value).split("-").map(Number);
    if (!year || !month || !day) {
      return null;
    }
    return new Date(year, month - 1, day);
  };

  const formatDate = (value) => {
    if (!value) {
      return "—";
    }
    return `${pad(value.getDate())}.${pad(value.getMonth() + 1)}.${value.getFullYear()}`;
  };

  const formatRange = (start, end) => {
    if (!start) {
      return "—";
    }
    if (!end) {
      return `${formatDate(start)} —`;
    }
    return `${formatDate(start)} — ${formatDate(end)}`;
  };

  const isSameDay = (left, right) =>
    left &&
    right &&
    left.getFullYear() === right.getFullYear() &&
    left.getMonth() === right.getMonth() &&
    left.getDate() === right.getDate();

  const isBetween = (value, start, end) => {
    if (!value || !start || !end) {
      return false;
    }
    return value >= start && value <= end;
  };

  const getCookie = (name) => {
    const cookieValue = document.cookie
      .split(";")
      .map((cookie) => cookie.trim())
      .find((cookie) => cookie.startsWith(`${name}=`));
    return cookieValue ? decodeURIComponent(cookieValue.split("=")[1]) : "";
  };

  const showMessage = (target, text, type) => {
    if (!target) {
      return;
    }
    target.textContent = text || "";
    target.classList.remove("is-error", "is-success", "is-warning");
    if (!text) {
      target.hidden = true;
      return;
    }
    target.hidden = false;
    if (type === "error") {
      target.classList.add("is-error");
    } else if (type === "warning") {
      target.classList.add("is-warning");
    } else {
      target.classList.add("is-success");
    }
  };

  const extractErrorMessage = (data) => {
    if (!data) {
      return "";
    }
    if (typeof data === "string") {
      return data;
    }
    if (Array.isArray(data)) {
      return data.join(" ");
    }
    if (data.detail) {
      return data.detail;
    }
    if (data.non_field_errors) {
      return Array.isArray(data.non_field_errors)
        ? data.non_field_errors.join(" ")
        : data.non_field_errors;
    }
    const firstKey = Object.keys(data)[0];
    if (!firstKey) {
      return "";
    }
    const value = data[firstKey];
    return Array.isArray(value) ? value.join(" ") : String(value);
  };

  const updateStatusPill = (status) => {
    const statusInfo = statusMap[status] || statusMap.active;
    if (statusPill) {
      statusPill.textContent = statusInfo.label;
      statusPill.className = "status-pill";
      if (statusInfo.className) {
        statusPill.classList.add(statusInfo.className);
      }
    }
    if (statusLabel) {
      statusLabel.textContent = statusInfo.label;
    }
  };

  const getStatusFromAbsences = (absences) => {
    if (!isActive) {
      return "inactive";
    }
    const today = toDateOnly(new Date());
    const current = absences.find((absence) => {
      const start = parseISODate(absence.start_date);
      const end = parseISODate(absence.end_date);
      return start && end && today >= start && today <= end;
    });
    if (current && current.absence_type) {
      return current.absence_type;
    }
    return "active";
  };

  const renderAbsenceList = (absences) => {
    if (!absenceList) {
      return;
    }
    if (!absences.length) {
      absenceList.innerHTML = '<div class="subtle">Нет данных.</div>';
      return;
    }
    const statusLabels = {
      current: "Текущий",
      upcoming: "Запланирован",
      past: "Завершен",
    };
    absenceList.innerHTML = "";
    absences.forEach((absence) => {
      const wrapper = document.createElement("div");
      wrapper.className = "absence-item";
      const title = document.createElement("div");
      title.className = "absence-item-title";
      title.textContent = absence.type_label || "Отсутствие";
      const meta = document.createElement("div");
      meta.className = "absence-item-meta";
      const start = parseISODate(absence.start_date);
      const end = parseISODate(absence.end_date);
      const days = absence.days || (start && end ? (end - start) / 86400000 + 1 : "");
      const daysLabel = days ? `${days} дн.` : "";
      const statusLabel = statusLabels[absence.status] || "";
      meta.textContent = `${formatRange(start, end)} ${daysLabel ? `· ${daysLabel}` : ""} ${statusLabel ? `· ${statusLabel}` : ""}`.trim();
      const pill = document.createElement("span");
      pill.className = "status-pill";
      if (absence.absence_type === "vacation") {
        pill.classList.add("warning");
      } else if (absence.absence_type === "sick") {
        pill.classList.add("danger");
      } else {
        pill.classList.add("muted");
      }
      pill.textContent = absence.type_label || "Отсутствие";
      const content = document.createElement("div");
      content.append(title, meta);
      wrapper.append(content, pill);
      absenceList.append(wrapper);
    });
  };

  const getVacationUsage = (year, absences) => {
    const vacations = absences.filter(
      (absence) => absence.absence_type === "vacation" && parseISODate(absence.start_date)?.getFullYear() === year
    );
    const daysUsed = vacations.reduce((sum, absence) => sum + (absence.days || 0), 0);
    return {
      daysUsed,
      periodsUsed: vacations.length,
      remainingDays: Math.max(0, 28 - daysUsed),
      remainingPeriods: Math.max(0, 2 - vacations.length),
    };
  };

  const getSelectionDays = () => {
    if (!rangeStart || !rangeEnd) {
      return 0;
    }
    return Math.round((rangeEnd - rangeStart) / 86400000) + 1;
  };

  const updateAbsenceMeta = () => {
    if (!absenceRange) {
      return;
    }
    const days = getSelectionDays();
    const rangeText = formatRange(rangeStart, rangeEnd);
    absenceRange.textContent = days ? `${rangeText} · ${days} дн.` : rangeText;
  };

  const updateAbsenceHint = () => {
    if (!absenceHint) {
      return;
    }
    absenceHint.textContent =
      currentAbsenceType === "vacation"
        ? "От 14 до 28 дней, максимум 2 периода"
        : "Можно отметить любой период";
  };

  const updateVacationBalance = () => {
    if (!vacationBalance || !vacationBalanceValue) {
      return;
    }
    if (currentAbsenceType !== "vacation") {
      vacationBalance.hidden = true;
      return;
    }
    const year = rangeStart ? rangeStart.getFullYear() : new Date().getFullYear();
    const usage = getVacationUsage(year, selectedAbsences);
    vacationBalance.hidden = false;
    vacationBalanceValue.textContent = `${usage.daysUsed} из 28 дней, периодов: ${usage.periodsUsed}/2`;
  };

  const validateSelection = () => {
    if (!rangeStart || !rangeEnd) {
      return { valid: false, message: "Выберите начало и конец периода." };
    }
    const overlap = selectedAbsences.some((absence) => {
      const start = parseISODate(absence.start_date);
      const end = parseISODate(absence.end_date);
      return start && end && rangeStart <= end && rangeEnd >= start;
    });
    if (overlap) {
      return { valid: false, message: "Период пересекается с уже отмеченным отсутствием." };
    }
    if (currentAbsenceType !== "vacation") {
      return { valid: true };
    }
    if (rangeStart.getFullYear() !== rangeEnd.getFullYear()) {
      return { valid: false, message: "Отпуск должен быть в пределах одного календарного года." };
    }
    const usage = getVacationUsage(rangeStart.getFullYear(), selectedAbsences);
    const daysSelected = getSelectionDays();
    if (daysSelected < 14) {
      return { valid: false, message: "Отпуск должен быть не менее 14 дней." };
    }
    if (usage.remainingPeriods <= 0) {
      return { valid: false, message: "Лимит отпусков на год уже исчерпан." };
    }
    if (daysSelected > 28) {
      return { valid: false, message: "Отпуск не может превышать 28 дней." };
    }
    if (daysSelected > usage.remainingDays) {
      return { valid: false, message: "Недостаточно доступных дней отпуска." };
    }
    return { valid: true };
  };

  const updateAbsenceState = () => {
    updateAbsenceMeta();
    updateVacationBalance();
    updateAbsenceHint();
    if (!rangeStart || !rangeEnd) {
      if (absenceSave) {
        absenceSave.disabled = true;
      }
      showMessage(absenceMessage, "", "success");
      return;
    }
    const validation = validateSelection();
    if (absenceSave) {
      absenceSave.disabled = !validation.valid;
    }
    if (!validation.valid) {
      showMessage(absenceMessage, validation.message, "warning");
    } else {
      showMessage(absenceMessage, "", "success");
    }
  };

  const renderCalendar = () => {
    if (!calendarGrid) {
      return;
    }
    calendarGrid.innerHTML = "";
    const monthStart = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth(), 1);
    const monthEnd = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth() + 1, 0);
    const monthLabel = monthStart.toLocaleString("ru-RU", { month: "long", year: "numeric" });
    if (calendarTitle) {
      calendarTitle.textContent = monthLabel.charAt(0).toUpperCase() + monthLabel.slice(1);
    }

    const startWeekday = (monthStart.getDay() + 6) % 7;
    const daysInMonth = monthEnd.getDate();
    const today = toDateOnly(new Date());

    const totalCells = Math.ceil((startWeekday + daysInMonth) / 7) * 7;

    for (let index = 0; index < totalCells; index += 1) {
      const dayNumber = index - startWeekday + 1;
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = "absence-day";

      if (dayNumber < 1 || dayNumber > daysInMonth) {
        cell.classList.add("is-empty");
        cell.disabled = true;
        cell.innerHTML = '<div class="absence-day-number">-</div>';
        calendarGrid.append(cell);
        continue;
      }

      const date = new Date(monthStart.getFullYear(), monthStart.getMonth(), dayNumber);
      const iso = toISODate(date);
      cell.dataset.date = iso;

      const absenceForDay = selectedAbsences.find((absence) => {
        const start = parseISODate(absence.start_date);
        const end = parseISODate(absence.end_date);
        return start && end && date >= start && date <= end;
      });

      if (absenceForDay) {
        if (absenceForDay.absence_type === "vacation") {
          cell.classList.add("is-vacation");
        } else if (absenceForDay.absence_type === "sick") {
          cell.classList.add("is-sick");
        }
        cell.classList.add("is-blocked");
        cell.disabled = true;
      }

      if (isSameDay(date, today)) {
        cell.classList.add("is-today");
      }

      if (rangeStart && isSameDay(date, rangeStart)) {
        cell.classList.add("is-selected");
      }
      if (rangeEnd && isSameDay(date, rangeEnd)) {
        cell.classList.add("is-selected");
      }
      if (rangeStart && rangeEnd && isBetween(date, rangeStart, rangeEnd)) {
        cell.classList.add("is-range");
      }

      cell.innerHTML = `<div class="absence-day-number">${dayNumber}</div>`;
      calendarGrid.append(cell);
    }
  };

  const loadAbsences = async () => {
    if (!absenceUrl) {
      return [];
    }
    try {
      const response = await fetch(absenceUrl, { credentials: "same-origin" });
      if (!response.ok) {
        throw new Error("load failed");
      }
      const data = await response.json();
      return Array.isArray(data) ? data : [];
    } catch (error) {
      return [];
    }
  };

  const resetSelection = () => {
    rangeStart = null;
    rangeEnd = null;
    renderCalendar();
    updateAbsenceState();
  };

  typeTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      typeTabs.forEach((btn) => btn.classList.remove("active"));
      tab.classList.add("active");
      currentAbsenceType = tab.dataset.absenceType || "vacation";
      resetSelection();
      updateAbsenceHint();
      updateVacationBalance();
    });
  });

  if (calendarPrev) {
    calendarPrev.addEventListener("click", () => {
      calendarMonth = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth() - 1, 1);
      renderCalendar();
    });
  }

  if (calendarNext) {
    calendarNext.addEventListener("click", () => {
      calendarMonth = new Date(calendarMonth.getFullYear(), calendarMonth.getMonth() + 1, 1);
      renderCalendar();
    });
  }

  if (calendarGrid) {
    calendarGrid.addEventListener("click", (event) => {
      const button = event.target.closest(".absence-day");
      if (!button || button.classList.contains("is-empty") || button.disabled) {
        return;
      }
      const dateValue = parseISODate(button.dataset.date);
      if (!dateValue) {
        return;
      }
      if (!rangeStart || (rangeStart && rangeEnd)) {
        rangeStart = dateValue;
        rangeEnd = null;
      } else if (dateValue < rangeStart) {
        rangeEnd = rangeStart;
        rangeStart = dateValue;
      } else {
        rangeEnd = dateValue;
      }
      renderCalendar();
      updateAbsenceState();
    });
  }

  if (absenceReset) {
    absenceReset.addEventListener("click", () => {
      resetSelection();
    });
  }

  if (absenceSave) {
    absenceSave.addEventListener("click", async () => {
      if (!rangeStart || !rangeEnd) {
        return;
      }
      const validation = validateSelection();
      if (!validation.valid) {
        showMessage(absenceMessage, validation.message, "warning");
        return;
      }
      if (!absenceUrl) {
        showMessage(absenceMessage, "Не удалось сохранить отсутствие.", "error");
        return;
      }
      try {
        const response = await fetch(absenceUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCookie("csrftoken"),
          },
          body: JSON.stringify({
            absence_type: currentAbsenceType,
            start_date: toISODate(rangeStart),
            end_date: toISODate(rangeEnd),
          }),
        });
        if (!response.ok) {
          const data = await response.json();
          const message = extractErrorMessage(data) || "Не удалось сохранить отсутствие.";
          showMessage(absenceMessage, message, "error");
          return;
        }
        const saved = await response.json();
        selectedAbsences = [saved, ...selectedAbsences].sort((a, b) =>
          a.start_date < b.start_date ? 1 : -1
        );
        renderAbsenceList(selectedAbsences);
        updateStatusPill(getStatusFromAbsences(selectedAbsences));
        resetSelection();
        showMessage(absenceMessage, "Отсутствие сохранено.", "success");
      } catch (error) {
        showMessage(absenceMessage, "Не удалось сохранить отсутствие.", "error");
      }
    });
  }

  const init = async () => {
    selectedAbsences = await loadAbsences();
    renderAbsenceList(selectedAbsences);
    updateStatusPill(getStatusFromAbsences(selectedAbsences));
    updateAbsenceHint();
    updateAbsenceState();
    renderCalendar();
  };

  init();
})();
