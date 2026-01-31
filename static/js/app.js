(() => {
  const root = document.documentElement;
  const themeToggle = document.querySelector("[data-action='toggle-theme']");
  const sidebarToggles = document.querySelectorAll("[data-action='toggle-sidebar']");
  const passwordToggles = document.querySelectorAll("[data-action='toggle-password']");
  const storage = (() => {
    try {
      const testKey = "__shift_storage__";
      window.localStorage.setItem(testKey, "1");
      window.localStorage.removeItem(testKey);
      return window.localStorage;
    } catch (err) {
      return null;
    }
  })();

  const getStored = (key) => (storage ? storage.getItem(key) : null);
  const setStored = (key, value) => {
    if (!storage) {
      return;
    }
    storage.setItem(key, value);
  };

  const updateThemeLabel = (theme) => {
    if (!themeToggle) {
      return;
    }
    const label = themeToggle.querySelector(".theme-label");
    if (label) {
      label.textContent = theme === "dark" ? "Темная тема" : "Светлая тема";
    }
  };

  const setTheme = (theme) => {
    root.dataset.theme = theme;
    setStored("theme", theme);
    updateThemeLabel(theme);
  };

  const storedTheme = getStored("theme");
  if (storedTheme) {
    setTheme(storedTheme);
  } else {
    const prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    setTheme(prefersDark ? "dark" : "light");
  }

  if (themeToggle) {
    themeToggle.addEventListener("click", () => {
      const nextTheme = root.dataset.theme === "dark" ? "light" : "dark";
      setTheme(nextTheme);
    });
  }

  const setSidebarCollapsed = (isCollapsed) => {
    document.body.classList.toggle("sidebar-collapsed", isCollapsed);
    setStored("sidebar-collapsed", String(isCollapsed));
    sidebarToggles.forEach((btn) => {
      btn.setAttribute("aria-pressed", String(isCollapsed));
    });
  };

  if (sidebarToggles.length) {
    const storedSidebar = getStored("sidebar-collapsed");
    const initialCollapsed =
      storedSidebar === null
        ? document.body.classList.contains("sidebar-collapsed")
        : storedSidebar === "true";
    setSidebarCollapsed(initialCollapsed);
    sidebarToggles.forEach((btn) => {
      btn.addEventListener("click", () => {
        const nextState = !document.body.classList.contains("sidebar-collapsed");
        setSidebarCollapsed(nextState);
      });
    });
  }

  if (passwordToggles.length) {
    passwordToggles.forEach((btn) => {
      const field = btn.closest(".password-input");
      const input = field ? field.querySelector("input") : null;
      if (!input) {
        return;
      }
      btn.addEventListener("click", () => {
        const isText = input.type === "text";
        input.type = isText ? "password" : "text";
        btn.classList.toggle("is-active", !isText);
        btn.setAttribute("aria-pressed", String(!isText));
        btn.setAttribute("aria-label", isText ? "Показать пароль" : "Скрыть пароль");
      });
    });
  }

  const getCookie = (name) => {
    const cookieValue = document.cookie
      .split(";")
      .map((cookie) => cookie.trim())
      .find((cookie) => cookie.startsWith(`${name}=`));
    return cookieValue ? decodeURIComponent(cookieValue.split("=")[1]) : "";
  };

  const modalFocusMap = new Map();

  document.querySelectorAll(".modal[aria-hidden='true']").forEach((modal) => {
    modal.setAttribute("inert", "");
    modal.setAttribute("hidden", "");
  });

  const syncBodyModalState = () => {
    const openModals = document.querySelectorAll(".modal.is-open");
    document.body.classList.toggle("modal-open", openModals.length > 0);
  };

  const openModal = (modal) => {
    if (!modal) {
      return;
    }
    modalFocusMap.set(modal, document.activeElement);
    modal.removeAttribute("hidden");
    modal.removeAttribute("inert");
    modal.classList.add("is-open");
    modal.setAttribute("aria-hidden", "false");
    syncBodyModalState();
    const focusTarget = modal.querySelector("input, select, button, textarea");
    if (focusTarget) {
      focusTarget.focus();
    }
  };

  const closeModal = (modal) => {
    if (!modal) {
      return;
    }
    const active = document.activeElement;
    if (active && modal.contains(active)) {
      active.blur();
    }
    modal.classList.remove("is-open");
    modal.setAttribute("aria-hidden", "true");
    modal.setAttribute("inert", "");
    modal.setAttribute("hidden", "");
    syncBodyModalState();
    const returnFocus = modalFocusMap.get(modal);
    if (returnFocus && typeof returnFocus.focus === "function") {
      returnFocus.focus();
    }
    modalFocusMap.delete(modal);
  };

  const closeModalButtons = document.querySelectorAll("[data-action='close-modal']");
  if (closeModalButtons.length) {
    closeModalButtons.forEach((button) => {
      button.addEventListener("click", (event) => {
        const modal = event.target.closest(".modal");
        closeModal(modal);
      });
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      document.querySelectorAll(".modal.is-open").forEach((modal) => closeModal(modal));
    }
  });

  const customSelects = new Set();
  let customSelectsReady = false;

  const closeAllSelects = (except) => {
    customSelects.forEach((wrapper) => {
      if (wrapper !== except) {
        wrapper.classList.remove("is-open");
      }
    });
  };

  const refreshCustomSelect = (wrapper) => {
    if (!wrapper) {
      return;
    }
    const select = wrapper.querySelector("select");
    const menu = wrapper.querySelector("[data-select-menu]");
    const label = wrapper.querySelector("[data-select-label]");
    if (!select || !menu || !label) {
      return;
    }
    const optionsRoot = menu.querySelector("[data-select-options]") || menu;
    optionsRoot.innerHTML = "";
    const searchInput = menu.querySelector("[data-select-search]");
    const query = searchInput ? searchInput.value.toLowerCase().trim() : "";
    let hasMatches = false;
    Array.from(select.options).forEach((option) => {
      if (query && !String(option.textContent || "").toLowerCase().includes(query)) {
        return;
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = "custom-select-option";
      button.dataset.value = option.value;
      button.textContent = option.textContent;
      if (option.disabled) {
        button.disabled = true;
      }
      if (option.selected) {
        button.classList.add("is-selected");
      }
      optionsRoot.append(button);
      hasMatches = true;
    });
    if (searchInput && !hasMatches) {
      const empty = document.createElement("div");
      empty.className = "custom-select-empty";
      empty.textContent = "Ничего не найдено";
      optionsRoot.append(empty);
    }
    const selectedOption = select.selectedOptions[0];
    label.textContent = selectedOption ? selectedOption.textContent : "";
    wrapper.classList.toggle("is-disabled", select.disabled);
  };

  const initCustomSelect = (wrapper) => {
    const select = wrapper.querySelector("select");
    const trigger = wrapper.querySelector("[data-select-trigger]");
    const menu = wrapper.querySelector("[data-select-menu]");
    const searchInput = menu ? menu.querySelector("[data-select-search]") : null;
    if (!select || !trigger || !menu) {
      return;
    }
    refreshCustomSelect(wrapper);

    menu.addEventListener(
      "wheel",
      (event) => {
        event.stopPropagation();
      },
      { passive: true },
    );

    trigger.addEventListener("click", () => {
      if (select.disabled) {
        return;
      }
      const isOpen = wrapper.classList.toggle("is-open");
      if (isOpen) {
        closeAllSelects(wrapper);
        if (searchInput) {
          searchInput.focus();
        }
      }
    });

    menu.addEventListener("click", (event) => {
      const optionButton = event.target.closest("[data-value]");
      if (!optionButton || optionButton.disabled) {
        return;
      }
      select.value = optionButton.dataset.value;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      refreshCustomSelect(wrapper);
      wrapper.classList.remove("is-open");
    });

    select.addEventListener("change", () => {
      refreshCustomSelect(wrapper);
    });

    if (searchInput) {
      searchInput.addEventListener("input", () => {
        refreshCustomSelect(wrapper);
      });
    }
  };

  const initCustomSelects = (root = document) => {
    const wrappers = Array.from(root.querySelectorAll("[data-custom-select]"));
    if (!wrappers.length) {
      return;
    }
    wrappers.forEach((wrapper) => {
      if (wrapper.dataset.customSelectReady === "true") {
        return;
      }
      wrapper.dataset.customSelectReady = "true";
      customSelects.add(wrapper);
      initCustomSelect(wrapper);
    });
    if (!customSelectsReady) {
      document.addEventListener("click", (event) => {
        if (!event.target.closest("[data-custom-select]")) {
          closeAllSelects();
        }
      });
      customSelectsReady = true;
    }
  };

  try {
    initCustomSelects();
  } catch (error) {
    // Allow the rest of the page to work even if custom select init fails.
  }

  const initTagInput = (wrapper) => {
    const input = wrapper.querySelector("[data-tag-field]");
    const addButton = wrapper.querySelector("[data-tag-add]");
    const list = wrapper.querySelector("[data-tag-list]");
    const storage = wrapper.querySelector("[data-tag-storage]");
    if (!input || !addButton || !list || !storage) {
      return;
    }

    const readTags = () =>
      Array.from(list.querySelectorAll("[data-tag-value]"))
        .map((item) => item.dataset.tagValue || "")
        .filter(Boolean);

    const writeTags = (tags) => {
      storage.value = JSON.stringify(tags);
    };

    const addTag = (value) => {
      const trimmed = String(value || "").replace(/\s+/g, " ").trim();
      if (!trimmed) {
        return;
      }
      const existing = readTags();
      const exists = existing.some((tag) => tag.toLowerCase() === trimmed.toLowerCase());
      if (exists) {
        input.value = "";
        return;
      }
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.dataset.tagValue = trimmed;
      tag.innerHTML = `
        <span class="tag-label">${trimmed}</span>
        <button class="tag-remove" type="button" aria-label="Удалить">×</button>
      `;
      list.append(tag);
      writeTags([...existing, trimmed]);
      input.value = "";
    };

    addButton.addEventListener("click", () => {
      addTag(input.value);
    });

    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        addTag(input.value);
      }
    });

    list.addEventListener("click", (event) => {
      const removeButton = event.target.closest(".tag-remove");
      if (!removeButton) {
        return;
      }
      const tag = removeButton.closest("[data-tag-value]");
      if (!tag) {
        return;
      }
      tag.remove();
      writeTags(readTags());
    });
  };

  const initTagInputs = (root = document) => {
    const wrappers = Array.from(root.querySelectorAll("[data-tag-input]"));
    wrappers.forEach((wrapper) => {
      if (wrapper.dataset.tagInputReady === "true") {
        return;
      }
      wrapper.dataset.tagInputReady = "true";
      initTagInput(wrapper);
    });
  };

  try {
    initTagInputs();
  } catch (error) {
    // Allow the rest of the page to work even if tag input init fails.
  }

  const adminUsers = document.querySelector("[data-admin-users]");
  if (adminUsers) {
    const employeesUrl = adminUsers.dataset.employeesUrl;
    const importUrl = adminUsers.dataset.importUrl;
    const detailBaseUrl = adminUsers.dataset.userDetailBase;
    const form = document.querySelector("[data-employee-form]");
    const importForm = document.querySelector("[data-import-form]");
    const importMessage = importForm ? importForm.querySelector("[data-import-message]") : null;
    const fileDrop = importForm ? importForm.querySelector("[data-file-drop]") : null;
    const fileInput = importForm ? importForm.querySelector("input[type='file']") : null;
    const fileName = importForm ? importForm.querySelector("[data-file-name]") : null;
    const tableBody = document.querySelector("[data-employee-rows]");
    const message = form ? form.querySelector("[data-form-message]") : null;
    const globalMessage = document.querySelector("[data-global-message]");
    const addUserModal = document.querySelector("[data-modal='add-user']");
    const importModal = document.querySelector("[data-modal='import-users']");
    const openAddUserButton = document.querySelector("[data-action='open-add-user']");
    const openImportButton = document.querySelector("[data-action='open-import']");
    const selectAllCheckbox = document.querySelector("[data-select-all]");
    const roleFilter = document.querySelector("[data-filter-role]");
    const departmentFilter = document.querySelector("[data-filter-department]");
    const searchInput = document.querySelector("[data-filter-search]");
    const pagination = document.querySelector("[data-pagination]");
    const paginationPages = pagination ? pagination.querySelector("[data-pagination-pages]") : null;
    const paginationPrev = pagination ? pagination.querySelector("[data-pagination-prev]") : null;
    const paginationNext = pagination ? pagination.querySelector("[data-pagination-next]") : null;
    const departmentSelect = document.querySelector("[data-department-select]");
    const positionSelect = document.querySelector("[data-position-select]");
    const roleSelect = document.querySelector("[data-role-select]");
    const phoneInput = document.querySelector("[data-phone-input]");
    const emailInput = document.querySelector("[data-email-input]");
    const bulkDeactivateUrl = adminUsers.dataset.bulkDeactivateUrl;
    const bulkDeactivateButton = document.querySelector("[data-bulk-deactivate]");
    const bulkActivateUrl = adminUsers.dataset.bulkActivateUrl;
    const bulkActivateButton = document.querySelector("[data-bulk-activate]");
    const bulkHint = document.querySelector("[data-bulk-hint]");
    const bulkModal = document.querySelector("[data-modal='bulk-deactivate']");
    const bulkConfirmButton = bulkModal ? bulkModal.querySelector("[data-bulk-confirm]") : null;
    const bulkActivateModal = document.querySelector("[data-modal='bulk-activate']");
    const bulkActivateConfirm = bulkActivateModal
      ? bulkActivateModal.querySelector("[data-bulk-activate-confirm]")
      : null;
    const resetRequests = document.querySelector("[data-reset-requests]");
    const resetResolveUrl = resetRequests ? resetRequests.dataset.resetResolveUrl : "";

    const showMessage = (text, type) => {
      if (!message) {
        return;
      }
      message.textContent = text || "";
      message.classList.remove("is-error", "is-success");
      if (!text) {
        message.hidden = true;
        return;
      }
      message.hidden = false;
      if (type) {
        message.classList.add(type === "error" ? "is-error" : "is-success");
      }
    };

    const showGlobalMessage = (text, type) => {
      if (!globalMessage) {
        return;
      }
      globalMessage.textContent = text || "";
      globalMessage.classList.remove("is-error", "is-success");
      if (!text) {
        globalMessage.hidden = true;
        return;
      }
      globalMessage.hidden = false;
      if (type) {
        globalMessage.classList.add(type === "error" ? "is-error" : "is-success");
      }
    };

    const formatDepartmentError = (data) => {
      if (!data || typeof data !== "object") {
        return "";
      }
      return Object.entries(data)
        .map(([key, value]) => {
          const labelMap = {
            name: "Название",
            manager_id: "Менеджер",
            positions: "Должности",
            non_field_errors: "Ошибка",
            detail: "Ошибка",
          };
          const label = labelMap[key] || key;
          const text = Array.isArray(value) ? value.join(" ") : String(value);
          return `${label}: ${text}`;
        })
        .join("\n");
    };

    const autoHideGlobalMessage = () => {
      if (!globalMessage) {
        return;
      }
      window.clearTimeout(globalMessage.dataset.timeoutId);
      const timeoutId = window.setTimeout(() => {
        globalMessage.hidden = true;
      }, 30000);
      globalMessage.dataset.timeoutId = String(timeoutId);
    };

    const errorLabels = {
      full_name: "ФИО",
      first_name: "Имя",
      last_name: "Фамилия",
      middle_name: "Отчество",
      email: "Email",
      corporate_phone: "Телефон",
      department_id: "Отдел",
      department_name: "Отдел",
      position: "Должность",
      role: "Роль",
      username: "Логин",
    };

    const formatErrorDetail = (data) => {
      if (!data || typeof data !== "object") {
        return "";
      }
      return Object.entries(data)
        .map(([key, value]) => {
          const label = errorLabels[key] || key;
          const text = Array.isArray(value) ? value.join(" ") : String(value);
          return `${label}: ${text}`;
        })
        .join("\n");
    };

    const formatRate = (value) => {
      if (!value) {
        return "—";
      }
      const numberValue = Number(value);
      if (Number.isNaN(numberValue)) {
        return value;
      }
      const formatter = new Intl.NumberFormat("ru-RU", {
        style: "currency",
        currency: "RUB",
        maximumFractionDigits: 0,
      });
      return `${formatter.format(numberValue)}/ч`;
    };

    const showImportMessage = (text, type) => {
      if (!importMessage) {
        return;
      }
      importMessage.textContent = text || "";
      importMessage.classList.remove("is-error", "is-success");
      if (!text) {
        importMessage.hidden = true;
        return;
      }
      importMessage.hidden = false;
      if (type) {
        importMessage.classList.add(type === "error" ? "is-error" : "is-success");
      }
    };

    const roleMap = {
      admin: "Администратор",
      manager: "Менеджер",
      employee: "Сотрудник",
      staff: "Сотрудник",
    };

    const formatRole = (employee) =>
      employee.role_display || roleMap[employee.role] || employee.role || "—";

    const normalizeText = (value) =>
      String(value || "")
        .toLowerCase()
        .replace(/\s+/g, " ")
        .trim();

    const buildDetailUrl = (id) => {
      if (!detailBaseUrl) {
        return "#";
      }
      return detailBaseUrl.replace(/0\/?$/, `${id}/`);
    };

    const renderEmployeeRow = (employee) => {
      const row = document.createElement("tr");
      const statusClass = employee.is_active ? "success" : "warning";
      row.dataset.id = String(employee.id || "");
      row.dataset.active = String(!!employee.is_active);
      row.dataset.role = employee.role || "";
      row.dataset.department = employee.department && employee.department !== "—" ? employee.department : "";
      row.dataset.name = normalizeText(employee.full_name || employee.username);
      row.innerHTML = `
        <td>
          <label class="checkbox">
            <input type="checkbox" data-select-row />
            <span class="checkbox-mark"></span>
          </label>
        </td>
        <td>${employee.full_name || employee.username}</td>
        <td>${employee.email || "—"}</td>
        <td>${employee.department || "—"}</td>
        <td>${formatRole(employee)}</td>
        <td>
          <span class="status-dot ${statusClass}" aria-hidden="true"></span>
          <span class="sr-only">${employee.status_label || ""}</span>
        </td>
        <td>
          <div class="table-actions">
            <a class="icon-action" href="${buildDetailUrl(employee.id)}" aria-label="Подробнее о сотруднике">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
                <circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.6" />
              </svg>
            </a>
          </div>
        </td>
      `;
      return row;
    };

    const getSelectedRows = () => {
      if (!tableBody) {
        return [];
      }
      return Array.from(tableBody.querySelectorAll("[data-select-row]:checked"))
        .map((checkbox) => checkbox.closest("tr"))
        .filter((row) => row && !row.hidden);
    };

    const getSelectedIds = (statusFilter) => {
      const rows = getSelectedRows();
      return rows
        .filter((row) => {
          if (!statusFilter) {
            return true;
          }
          return row.dataset.active === statusFilter;
        })
        .map((row) => row.dataset.id)
        .filter(Boolean);
    };

    const updateBulkActionState = () => {
      if (!bulkHint || !bulkDeactivateButton || !bulkActivateButton) {
        return;
      }
      const selectedRows = getSelectedRows();
      const activeRows = selectedRows.filter((row) => row.dataset.active === "true");
      const inactiveRows = selectedRows.filter((row) => row.dataset.active === "false");
      if (selectedRows.length) {
        bulkHint.hidden = true;
        bulkDeactivateButton.hidden = activeRows.length === 0;
        bulkActivateButton.hidden = inactiveRows.length === 0;
        if (activeRows.length) {
          bulkDeactivateButton.textContent = `Деактивировать (${activeRows.length})`;
        }
        if (inactiveRows.length) {
          bulkActivateButton.textContent = `Активировать (${inactiveRows.length})`;
        }
      } else {
        bulkActivateButton.hidden = true;
        bulkDeactivateButton.hidden = true;
        bulkHint.hidden = false;
        bulkDeactivateButton.textContent = "Деактивировать";
        bulkActivateButton.textContent = "Активировать";
      }
    };

    const buildResetResolveUrl = (id) => {
      if (!resetResolveUrl) {
        return "";
      }
      return resetResolveUrl.replace(/0\/resolve\/?$/, `${id}/resolve/`);
    };

    if (openAddUserButton) {
      openAddUserButton.addEventListener("click", () => openModal(addUserModal));
    }

    if (openImportButton) {
      openImportButton.addEventListener("click", () => openModal(importModal));
    }

    if (bulkDeactivateButton && bulkModal) {
      bulkDeactivateButton.addEventListener("click", () => openModal(bulkModal));
    }

    if (bulkActivateButton && bulkActivateModal) {
      bulkActivateButton.addEventListener("click", () => openModal(bulkActivateModal));
    }

    if (bulkConfirmButton) {
      bulkConfirmButton.addEventListener("click", async () => {
        const selectedIds = getSelectedIds("true");
        if (!selectedIds.length) {
          showGlobalMessage("Выберите сотрудников.", "error");
          autoHideGlobalMessage();
          closeModal(bulkModal);
          return;
        }
        if (!bulkDeactivateUrl) {
          showGlobalMessage("URL деактивации не задан.", "error");
          autoHideGlobalMessage();
          closeModal(bulkModal);
          return;
        }
        try {
          const response = await fetch(bulkDeactivateUrl, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": getCookie("csrftoken"),
            },
            credentials: "same-origin",
            body: JSON.stringify({ ids: selectedIds }),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            showGlobalMessage(data.detail || "Не удалось деактивировать сотрудников.", "error");
            autoHideGlobalMessage();
            return;
          }

          const updatedIds = Array.isArray(data.updated_ids) ? data.updated_ids.map(String) : [];
          updatedIds.forEach((id) => {
            const row = tableBody ? tableBody.querySelector(`tr[data-id='${id}']`) : null;
            if (!row) {
              return;
            }
            const dot = row.querySelector(".status-dot");
            if (dot) {
              dot.classList.remove("success");
              dot.classList.add("warning");
            }
            row.dataset.active = "false";
            const sr = row.querySelector(".sr-only");
            if (sr) {
              sr.textContent = "Деактивирован";
            }
            const checkbox = row.querySelector("[data-select-row]");
            if (checkbox) {
              checkbox.checked = false;
            }
          });
          updateSelectAllState();
          showGlobalMessage("Сотрудники деактивированы. Они потеряли доступ к сайту.", "success");
          autoHideGlobalMessage();
          closeModal(bulkModal);
        } catch (error) {
          showGlobalMessage("Ошибка сети. Попробуйте позже.", "error");
          autoHideGlobalMessage();
        }
      });
    }

    if (bulkActivateConfirm) {
      bulkActivateConfirm.addEventListener("click", async () => {
        const selectedIds = getSelectedIds("false");
        if (!selectedIds.length) {
          showGlobalMessage("Выберите деактивированных сотрудников.", "error");
          autoHideGlobalMessage();
          closeModal(bulkActivateModal);
          return;
        }
        if (!bulkActivateUrl) {
          showGlobalMessage("URL активации не задан.", "error");
          autoHideGlobalMessage();
          closeModal(bulkActivateModal);
          return;
        }
        try {
          const response = await fetch(bulkActivateUrl, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": getCookie("csrftoken"),
            },
            credentials: "same-origin",
            body: JSON.stringify({ ids: selectedIds }),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            showGlobalMessage(data.detail || "Не удалось активировать сотрудников.", "error");
            autoHideGlobalMessage();
            return;
          }

          const updatedIds = Array.isArray(data.updated_ids) ? data.updated_ids.map(String) : [];
          updatedIds.forEach((id) => {
            const row = tableBody ? tableBody.querySelector(`tr[data-id='${id}']`) : null;
            if (!row) {
              return;
            }
            const dot = row.querySelector(".status-dot");
            if (dot) {
              dot.classList.remove("warning");
              dot.classList.add("success");
            }
            row.dataset.active = "true";
            const sr = row.querySelector(".sr-only");
            if (sr) {
              sr.textContent = "Активен";
            }
            const checkbox = row.querySelector("[data-select-row]");
            if (checkbox) {
              checkbox.checked = false;
            }
          });
          updateSelectAllState();
          showGlobalMessage(
            "Сотрудники активированы. Доступ восстановлен. Вход по старым логину и паролю.",
            "success",
          );
          autoHideGlobalMessage();
          closeModal(bulkActivateModal);
        } catch (error) {
          showGlobalMessage("Ошибка сети. Попробуйте позже.", "error");
          autoHideGlobalMessage();
        }
      });
    }

    if (resetRequests && resetResolveUrl) {
      resetRequests.addEventListener("click", async (event) => {
        const button = event.target.closest("[data-request-resolve]");
        if (!button) {
          return;
        }
        const row = button.closest("[data-request-id]");
        if (!row) {
          return;
        }
        const requestId = row.dataset.requestId;
        if (!requestId) {
          return;
        }
        const resolveUrl = buildResetResolveUrl(requestId);
        try {
          const response = await fetch(resolveUrl, {
            method: "POST",
            headers: {
              "X-CSRFToken": getCookie("csrftoken"),
            },
            credentials: "same-origin",
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            showGlobalMessage(data.detail || "Не удалось восстановить доступ.", "error");
            autoHideGlobalMessage();
            return;
          }

          row.remove();
          if (!resetRequests.querySelector("[data-request-id]")) {
            const empty = resetRequests.querySelector(".reset-empty");
            if (empty) {
              empty.hidden = false;
            } else {
              const emptyNode = document.createElement("div");
              emptyNode.className = "reset-empty subtle";
              emptyNode.textContent = "Запросов нет.";
              const list = resetRequests.querySelector(".reset-requests-list");
              if (list) {
                list.append(emptyNode);
              }
            }
          }
          const messageLines = ["Доступ восстановлен."];
          if (data.password_sent) {
            messageLines.push("Письмо с логином и паролем отправлено.");
          } else {
            messageLines.push("Письмо не отправлено.");
          }
          if (data.email_error) {
            messageLines.push(`Ошибка отправки: ${data.email_error}`);
          }
          showGlobalMessage(messageLines.join("\n"), data.password_sent ? "success" : "error");
          autoHideGlobalMessage();
        } catch (error) {
          showGlobalMessage("Ошибка сети. Попробуйте позже.", "error");
          autoHideGlobalMessage();
        }
      });
    }


    const updateSelectAllState = () => {
      if (!selectAllCheckbox || !tableBody) {
        return;
      }
      const checkboxes = Array.from(tableBody.querySelectorAll("[data-select-row]")).filter(
        (checkbox) => !checkbox.closest("tr").hidden,
      );
      if (!checkboxes.length) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
        return;
      }
      const checked = checkboxes.filter((checkbox) => checkbox.checked);
      selectAllCheckbox.checked = checked.length === checkboxes.length;
      selectAllCheckbox.indeterminate = checked.length > 0 && checked.length < checkboxes.length;
      updateBulkActionState();
    };

    if (selectAllCheckbox && tableBody) {
      selectAllCheckbox.addEventListener("change", () => {
        const isChecked = selectAllCheckbox.checked;
        tableBody
          .querySelectorAll("[data-select-row]")
          .forEach((checkbox) => {
            if (!checkbox.closest("tr").hidden) {
              checkbox.checked = isChecked;
            }
          });
        updateSelectAllState();
      });

      tableBody.addEventListener("change", (event) => {
        if (event.target.matches("[data-select-row]")) {
          updateSelectAllState();
        }
      });
    }

    const pageSize = 8;
    let currentPage = 1;

    const getDataRows = () =>
      tableBody ? Array.from(tableBody.querySelectorAll("tr")).filter((row) => !row.dataset.emptyRow) : [];

    const emptyRow = tableBody ? tableBody.querySelector("[data-empty-row]") : null;

    const renderPagination = (totalPages) => {
      if (!pagination || !paginationPages) {
        return;
      }
      paginationPages.innerHTML = "";
      pagination.hidden = totalPages <= 1;
      for (let i = 1; i <= totalPages; i += 1) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "pagination-page";
        button.textContent = String(i);
        if (i === currentPage) {
          button.classList.add("is-active");
        }
        button.addEventListener("click", () => {
          currentPage = i;
          applyFiltersAndPagination();
        });
        paginationPages.append(button);
      }
      if (paginationPrev) {
        paginationPrev.disabled = currentPage <= 1;
      }
      if (paginationNext) {
        paginationNext.disabled = currentPage >= totalPages;
      }
    };

    const applyFiltersAndPagination = () => {
      if (!tableBody) {
        return;
      }
      const roleValue = roleFilter ? roleFilter.value : "";
      const departmentValue = departmentFilter ? departmentFilter.value : "";
      const searchValue = searchInput ? normalizeText(searchInput.value) : "";
      const rows = getDataRows();
      const filteredRows = rows.filter((row) => {
        const matchesRole = !roleValue || row.dataset.role === roleValue;
        const matchesDepartment = !departmentValue || row.dataset.department === departmentValue;
        const matchesSearch =
          !searchValue || normalizeText(row.dataset.name || row.textContent).includes(searchValue);
        return matchesRole && matchesDepartment && matchesSearch;
      });

      const totalPages = Math.max(1, Math.ceil(filteredRows.length / pageSize));
      currentPage = Math.min(currentPage, totalPages);
      const startIndex = (currentPage - 1) * pageSize;
      const endIndex = startIndex + pageSize;

      rows.forEach((row) => {
        row.hidden = true;
      });

      filteredRows.forEach((row, index) => {
        row.hidden = index < startIndex || index >= endIndex;
      });

      if (emptyRow) {
        emptyRow.hidden = filteredRows.length > 0;
      }

      renderPagination(totalPages);
      updateSelectAllState();
    };

    if (roleFilter) {
      roleFilter.addEventListener("change", applyFiltersAndPagination);
    }

    if (departmentFilter) {
      departmentFilter.addEventListener("change", applyFiltersAndPagination);
    }

    if (searchInput) {
      searchInput.addEventListener("input", () => {
        currentPage = 1;
        applyFiltersAndPagination();
      });
    }

    if (paginationPrev) {
      paginationPrev.addEventListener("click", () => {
        currentPage = Math.max(1, currentPage - 1);
        applyFiltersAndPagination();
      });
    }

    if (paginationNext) {
      paginationNext.addEventListener("click", () => {
        currentPage += 1;
        applyFiltersAndPagination();
      });
    }

    const departmentPositionsEl = document.getElementById("department-positions-data");
    const departmentPositions = departmentPositionsEl
      ? JSON.parse(departmentPositionsEl.textContent)
      : {};

    const employeeRole = detailRoot.dataset.employeeRole || "";

    const updatePositionOptions = () => {
      if (!departmentSelect || !positionSelect) {
        return;
      }
      const isManagerRole = roleSelect && roleSelect.value === "manager";
      const selectedOption = departmentSelect.selectedOptions[0];
      const departmentName = selectedOption ? selectedOption.dataset.name : "";
      const positions = departmentName ? departmentPositions[departmentName] || [] : [];

      positionSelect.innerHTML = "";
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = positions.length ? "Выберите должность" : "Нет доступных должностей";
      positionSelect.append(placeholder);

      positions.forEach((position) => {
        const option = document.createElement("option");
        option.value = position;
        option.textContent = position;
        positionSelect.append(option);
      });

      if (isManagerRole) {
        positionSelect.value = "";
        positionSelect.disabled = true;
        positionSelect.required = false;
        placeholder.textContent = "Не требуется для менеджера";
      } else {
        positionSelect.disabled = positions.length === 0;
        positionSelect.required = positions.length > 0;
      }
      const wrapper = positionSelect.closest("[data-custom-select]");
      refreshCustomSelect(wrapper);
    };

    if (departmentSelect) {
      departmentSelect.addEventListener("change", () => {
        updatePositionOptions();
        applyFiltersAndPagination();
      });
      updatePositionOptions();
    }
    if (roleSelect) {
      roleSelect.addEventListener("change", () => {
        updatePositionOptions();
      });
      refreshCustomSelect(roleSelect.closest("[data-custom-select]"));
    }

    const emailRegex = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;

    const normalizePhoneDigits = (value) => {
      let digits = String(value || "").replace(/\D/g, "");
      if (!digits) {
        return "";
      }
      if (digits.startsWith("8")) {
        digits = `7${digits.slice(1)}`;
      }
      if (digits.startsWith("9")) {
        digits = `7${digits}`;
      }
      if (digits.length === 10 && !digits.startsWith("7")) {
        digits = `7${digits}`;
      }
      return digits;
    };

    const isValidPhone = (value) => {
      const digits = normalizePhoneDigits(value);
      return digits.length === 11 && digits.startsWith("7");
    };

    const formatPhone = (value) => {
      const digits = normalizePhoneDigits(value);
      if (!digits) {
        return "";
      }
      const normalized = digits.slice(0, 11);
      const parts = normalized.slice(1);
      const area = parts.slice(0, 3);
      const first = parts.slice(3, 6);
      const second = parts.slice(6, 8);
      const third = parts.slice(8, 10);
      let formatted = "+7";
      if (area) {
        formatted += ` (${area}`;
      }
      if (area.length === 3) {
        formatted += ")";
      }
      if (first) {
        formatted += ` ${first}`;
      }
      if (second) {
        formatted += `-${second}`;
      }
      if (third) {
        formatted += `-${third}`;
      }
      return formatted;
    };

    if (phoneInput) {
      phoneInput.addEventListener("input", () => {
        phoneInput.value = formatPhone(phoneInput.value);
      });
    }

    if (emailInput) {
      emailInput.addEventListener("input", () => {
        emailInput.value = emailInput.value.replace(/[^A-Za-z0-9._%+-@]/g, "").toLowerCase();
      });
    }

    let selectedImportFile = null;
    if (fileDrop && fileInput) {
      const updateFileName = (file) => {
        if (fileName) {
          fileName.textContent = file ? file.name : "Файл не выбран";
        }
      };

      fileDrop.addEventListener("dragover", (event) => {
        event.preventDefault();
        fileDrop.classList.add("is-dragover");
      });

      fileDrop.addEventListener("dragleave", () => {
        fileDrop.classList.remove("is-dragover");
      });

      fileDrop.addEventListener("drop", (event) => {
        event.preventDefault();
        fileDrop.classList.remove("is-dragover");
        if (event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0]) {
          selectedImportFile = event.dataTransfer.files[0];
          updateFileName(selectedImportFile);
        }
      });

      fileInput.addEventListener("change", () => {
        selectedImportFile = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
        updateFileName(selectedImportFile);
      });
      updateFileName(selectedImportFile);
    }

    const validateEmployeeForm = (formElement) => {
      if (!formElement) {
        return "";
      }
      const firstName = formElement.querySelector("[name='first_name']")?.value.trim();
      const lastName = formElement.querySelector("[name='last_name']")?.value.trim();
      const email = formElement.querySelector("[name='email']")?.value.trim();
      const phone = formElement.querySelector("[name='corporate_phone']")?.value.trim();
      const departmentId = formElement.querySelector("[name='department_id']")?.value;
      const positionValue = formElement.querySelector("[name='position']")?.value;
      const roleValue = formElement.querySelector("[name='role']")?.value;
      const hourlyRateValue = formElement.querySelector("[name='hourly_rate']")?.value.trim();

      if (!lastName || !firstName) {
        return "Укажите фамилию и имя сотрудника.";
      }
      if (!email) {
        return "Укажите email сотрудника.";
      }
      if (!emailRegex.test(email)) {
        return "Email должен быть на латинице в формате name@example.com.";
      }
      if (phone && !isValidPhone(phone)) {
        return "Телефон должен быть в формате +7 (900) 000-00-00.";
      }
      if (hourlyRateValue) {
        const normalizedRate = hourlyRateValue.replace(",", ".");
        const [intPart] = normalizedRate.split(".");
        if (intPart && intPart.replace(/\D/g, "").length > 4) {
          return "Ставка не должна содержать больше 4 цифр.";
        }
        const rateNumber = Number(normalizedRate);
        if (Number.isNaN(rateNumber)) {
          return "Некорректная ставка.";
        }
        if (rateNumber > 9999) {
          return "Ставка не должна быть больше 9999.";
        }
      }
      if (!departmentId) {
        return "Выберите отдел.";
      }
      if (roleValue !== "manager" && positionSelect && !positionSelect.disabled && !positionValue) {
        return "Выберите должность.";
      }
      return "";
    };

    const mapRoleToCode = (role) => {
      const normalized = normalizeText(role);
      if (["администратор", "admin"].includes(normalized)) return "admin";
      if (["менеджер", "manager"].includes(normalized)) return "manager";
      return "employee";
    };

    const createEmployee = async (payload) => {
      const response = await fetch(employeesUrl, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCookie("csrftoken"),
        },
        credentials: "same-origin",
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = formatErrorDetail(data) || "Не удалось создать сотрудника.";
        throw new Error(detail);
      }
      return {
        user: data.user || data,
        passwordSent: Boolean(data.password_sent),
      };
    };

    if (form && employeesUrl) {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submitButton = form.querySelector("button[type='submit']");
        if (submitButton) {
          submitButton.disabled = true;
        }
        showMessage("", null);

        const validationError = validateEmployeeForm(form);
        if (validationError) {
          showMessage(validationError, "error");
          showGlobalMessage(validationError, "error");
          autoHideGlobalMessage();
          if (submitButton) {
            submitButton.disabled = false;
          }
          return;
        }

        const formData = new FormData(form);
        const payload = {};
        formData.forEach((value, key) => {
          if (typeof value === "string") {
            const trimmed = value.trim();
            if (trimmed !== "") {
              payload[key] = trimmed;
            }
          } else {
            payload[key] = value;
          }
        });

        if (payload.department_id) {
          payload.department_id = Number(payload.department_id);
        }
        if (payload.hourly_rate) {
          payload.hourly_rate = Number(payload.hourly_rate);
        }

        try {
          const response = await fetch(employeesUrl, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": getCookie("csrftoken"),
            },
            credentials: "same-origin",
            body: JSON.stringify(payload),
          });

          const data = await response.json();
          if (!response.ok) {
            const detail = formatErrorDetail(data) || "Не удалось создать сотрудника.";
            showMessage(detail, "error");
          showGlobalMessage(detail, "error");
          autoHideGlobalMessage();
            return;
          }

          showMessage("Сотрудник создан.", "success");
          const summaryLines = ["Сотрудник успешно добавлен."];
          if (data.password_sent) {
            summaryLines.push("Письмо с логином и паролем отправлено.");
          } else {
            summaryLines.push("Письмо с логином и паролем не отправлено.");
          }
          if (data.email_error) {
            summaryLines.push(`Ошибка отправки: ${data.email_error}`);
          }
          if (data.temporary_password) {
            summaryLines.push("Временный пароль отправлен на почту.");
          }
          showGlobalMessage(summaryLines.join("\n"), data.password_sent ? "success" : "error");
          autoHideGlobalMessage();
          form.reset();
          if (addUserModal) {
            closeModal(addUserModal);
          }
          if (positionSelect) {
            updatePositionOptions();
          }
          const createdUser = data.user || data;
          if (tableBody && createdUser) {
            currentPage = 1;
            tableBody.prepend(renderEmployeeRow(createdUser));
            applyFiltersAndPagination();
          } else {
            window.location.reload();
          }
        } catch (error) {
          showMessage("Ошибка сети. Попробуйте позже.", "error");
          showGlobalMessage("Ошибка сети. Попробуйте позже.", "error");
          autoHideGlobalMessage();
        } finally {
          if (submitButton) {
            submitButton.disabled = false;
          }
        }
      });
    }

    if (importForm) {
      importForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        showImportMessage("", null);
        if (!importUrl) {
          showImportMessage("URL импорта не задан.", "error");
          return;
        }
        const file = selectedImportFile || (fileInput && fileInput.files ? fileInput.files[0] : null);
        if (!file) {
          showImportMessage("Выберите файл для импорта.", "error");
          return;
        }
        const extension = file.name.split(".").pop().toLowerCase();
        if (!["csv", "xlsx", "xls"].includes(extension)) {
          showImportMessage("Поддерживаются файлы XLSX, XLS, CSV.", "error");
          return;
        }

        const formData = new FormData();
        formData.append("file", file);

        try {
          const response = await fetch(importUrl, {
            method: "POST",
            headers: {
              "X-CSRFToken": getCookie("csrftoken"),
            },
            credentials: "same-origin",
            body: formData,
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            showImportMessage(data.detail || "Не удалось импортировать файл.", "error");
            return;
          }

          const created = Array.isArray(data.created) ? data.created : [];
          const errors = Array.isArray(data.errors) ? data.errors : [];
          created.forEach((user) => {
            if (tableBody && user) {
              tableBody.prepend(renderEmployeeRow(user));
            }
          });

          applyFiltersAndPagination();

          if (errors.length) {
            const preview = errors.slice(0, 5).join("\n");
            showImportMessage(
              `Импорт завершен: добавлено ${data.created_count || created.length}, ошибок ${errors.length}.\n${preview}`,
              "error",
            );
            return;
          }

          const sent = data.passwords_sent || 0;
          showImportMessage(
            `Добавлено сотрудников: ${data.created_count || created.length}. Писем отправлено: ${sent}.`,
            "success",
          );
          if (fileInput) {
            fileInput.value = "";
          }
          selectedImportFile = null;
          if (fileName) {
            fileName.textContent = "Файл не выбран";
          }
          closeModal(importModal);
        } catch (error) {
          showImportMessage("Ошибка сети. Попробуйте позже.", "error");
        }
      });
    }

    applyFiltersAndPagination();
  }

  const adminDepartments = document.querySelector("[data-admin-departments]");
  if (adminDepartments) {
    const departmentsUrl = adminDepartments.dataset.departmentsUrl || "";
    const archiveBaseUrl = adminDepartments.dataset.departmentArchiveBase || "";
    const form = document.querySelector("[data-department-form]");
    const message = form ? form.querySelector("[data-form-message]") : null;
    const globalMessage = document.querySelector("[data-global-message]");
    const addDepartmentModal = document.querySelector("[data-modal='add-department']");
    const openAddDepartmentButton = document.querySelector("[data-action='open-add-department']");
    const archiveButton = document.querySelector("[data-action='toggle-department-archive']");
    const cardGrid = document.querySelector("[data-department-cards]");
    const departmentsDataEl = document.getElementById("departments-data");
    let departmentsData = [];
    let selectedDepartmentId = null;

    if (departmentsDataEl) {
      try {
        departmentsData = JSON.parse(departmentsDataEl.textContent);
      } catch (error) {
        departmentsData = [];
      }
    }

    const showMessage = (text, type) => {
      if (!message) {
        return;
      }
      message.textContent = text || "";
      message.classList.remove("is-error", "is-success");
      if (!text) {
        message.hidden = true;
        return;
      }
      message.hidden = false;
      if (type) {
        message.classList.add(type === "error" ? "is-error" : "is-success");
      }
    };

    const showGlobalMessage = (text, type) => {
      if (!globalMessage) {
        return;
      }
      globalMessage.textContent = text || "";
      globalMessage.classList.remove("is-error", "is-success");
      if (!text) {
        globalMessage.hidden = true;
        return;
      }
      globalMessage.hidden = false;
      if (type) {
        globalMessage.classList.add(type === "error" ? "is-error" : "is-success");
      }
    };

    const validateDepartmentForm = (formElement) => {
      const name = String(formElement.querySelector("[name='name']")?.value || "").trim();
      const managerSelect = formElement.querySelector("[name='manager_id']");
      const managerId = managerSelect ? managerSelect.value : "";
      if (!name) {
        return "Укажите название отдела.";
      }
      if (managerSelect && !managerSelect.disabled && !managerId) {
        return "Выберите менеджера.";
      }
      return "";
    };

    const readPositions = (formElement) => {
      const storage = formElement.querySelector("[data-tag-storage]");
      if (!storage || !storage.value) {
        return [];
      }
      try {
        const parsed = JSON.parse(storage.value);
        return Array.isArray(parsed) ? parsed : [];
      } catch (error) {
        return [];
      }
    };

    const buildArchiveUrl = (id) => {
      if (!archiveBaseUrl) {
        return "";
      }
      return archiveBaseUrl.replace(/0\/archive\/?$/, `${id}/archive/`);
    };

    const findDepartment = (id) =>
      departmentsData.find((item) => Number(item.id) === Number(id));

    const updateArchiveButton = () => {
      if (!archiveButton) {
        return;
      }
      const department = selectedDepartmentId ? findDepartment(selectedDepartmentId) : null;
      if (!department) {
        archiveButton.textContent = "Архивация отдела";
        archiveButton.disabled = true;
        return;
      }
      archiveButton.disabled = false;
      archiveButton.textContent = department.is_archived ? "Разархивировать" : "Архивировать";
    };

    if (openAddDepartmentButton && addDepartmentModal) {
      openAddDepartmentButton.addEventListener("click", () => openModal(addDepartmentModal));
    }

    if (cardGrid) {
      cardGrid.addEventListener("click", (event) => {
        if (event.target.closest("[data-detail-link]")) {
          return;
        }
        const card = event.target.closest("[data-department-card]");
        if (!card) {
          return;
        }
        const id = card.dataset.departmentId;
        if (!id) {
          return;
        }
        const previous = cardGrid.querySelector(".mini-card.is-selected");
        if (previous) {
          previous.classList.remove("is-selected");
        }
        card.classList.add("is-selected");
        selectedDepartmentId = id;
        updateArchiveButton();
      });
    }

    if (archiveButton) {
      archiveButton.addEventListener("click", async () => {
        if (!selectedDepartmentId) {
          return;
        }
        const department = findDepartment(selectedDepartmentId);
        if (!department) {
          return;
        }
        const archiveUrl = buildArchiveUrl(selectedDepartmentId);
        if (!archiveUrl) {
          showGlobalMessage("URL архивации не задан.", "error");
          return;
        }
        const nextState = !department.is_archived;
        archiveButton.disabled = true;
        try {
          const response = await fetch(archiveUrl, {
            method: "PATCH",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": getCookie("csrftoken"),
            },
            credentials: "same-origin",
            body: JSON.stringify({ is_archived: nextState }),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            const detail = data.detail || "Не удалось обновить статус отдела.";
            showGlobalMessage(detail, "error");
            return;
          }
          if (data && typeof data === "object") {
            Object.assign(department, data);
          } else {
            department.is_archived = nextState;
          }
          const card = cardGrid
            ? cardGrid.querySelector(`[data-department-id='${selectedDepartmentId}']`)
            : null;
          if (card) {
            card.classList.toggle("is-archived", !!department.is_archived);
          }
          updateArchiveButton();
          showGlobalMessage(
            department.is_archived ? "Отдел архивирован." : "Отдел разархивирован.",
            "success",
          );
        } catch (error) {
          showGlobalMessage("Ошибка сети. Попробуйте позже.", "error");
        } finally {
          archiveButton.disabled = false;
        }
      });
    }

    if (form && departmentsUrl) {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const submitButton = form.querySelector("button[type='submit']");
        if (submitButton) {
          submitButton.disabled = true;
        }
        showMessage("", null);
        showGlobalMessage("", null);

        const validationError = validateDepartmentForm(form);
        if (validationError) {
          showMessage(validationError, "error");
          showGlobalMessage(validationError, "error");
          if (submitButton) {
            submitButton.disabled = false;
          }
          return;
        }

        const formData = new FormData(form);
        const payload = {};
        formData.forEach((value, key) => {
          if (typeof value === "string") {
            const trimmed = value.trim();
            if (trimmed !== "") {
              payload[key] = trimmed;
            }
          } else {
            payload[key] = value;
          }
        });

        const positions = readPositions(form);
        payload.positions = positions;
        if (payload.manager_id) {
          payload.manager_id = Number(payload.manager_id);
        } else {
          delete payload.manager_id;
        }

        try {
          const response = await fetch(departmentsUrl, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": getCookie("csrftoken"),
            },
            credentials: "same-origin",
            body: JSON.stringify(payload),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            const detail = formatDepartmentError(data) || data.detail || "Не удалось создать отдел.";
            showMessage(detail, "error");
            showGlobalMessage(detail, "error");
            return;
          }
          showMessage("Отдел создан.", "success");
          showGlobalMessage("Отдел создан и добавлен в каталог.", "success");
          form.reset();
          const managerSelect = form.querySelector("[name='manager_id']");
          if (managerSelect) {
            managerSelect.value = "";
            refreshCustomSelect(managerSelect.closest("[data-custom-select]"));
          }
          const tagStorage = form.querySelector("[data-tag-storage]");
          const tagList = form.querySelector("[data-tag-list]");
          if (tagStorage) {
            tagStorage.value = "[]";
          }
          if (tagList) {
            tagList.innerHTML = "";
          }
          if (addDepartmentModal) {
            closeModal(addDepartmentModal);
          }
          window.setTimeout(() => {
            window.location.reload();
          }, 400);
        } catch (error) {
          showMessage("Ошибка сети. Попробуйте позже.", "error");
          showGlobalMessage("Ошибка сети. Попробуйте позже.", "error");
        } finally {
          if (submitButton) {
            submitButton.disabled = false;
          }
        }
      });
    }
  }

  const loginPage = document.querySelector("[data-login-page]");
  if (loginPage) {
    const resetModal = document.querySelector("[data-modal='reset-password']");
    const openResetButton = document.querySelector("[data-action='open-reset']");
    const resetForm = document.querySelector("[data-reset-form]");
    const resetMessage = resetForm ? resetForm.querySelector("[data-reset-message]") : null;
    const resetUrl = resetForm ? resetForm.dataset.resetUrl : "";

    const showResetMessage = (text, type) => {
      if (!resetMessage) {
        return;
      }
      resetMessage.textContent = text || "";
      resetMessage.classList.remove("is-error", "is-success");
      if (!text) {
        resetMessage.hidden = true;
        return;
      }
      resetMessage.hidden = false;
      if (type) {
        resetMessage.classList.add(type === "error" ? "is-error" : "is-success");
      }
    };

    if (openResetButton && resetModal) {
      openResetButton.addEventListener("click", () => openModal(resetModal));
    }

    if (resetForm) {
      resetForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        showResetMessage("", null);
        if (!resetUrl) {
          showResetMessage("URL запроса не задан.", "error");
          return;
        }
        const formData = new FormData(resetForm);
        const email = String(formData.get("email") || "").trim();
        if (!email) {
          showResetMessage("Введите корпоративную почту.", "error");
          return;
        }
        try {
          const response = await fetch(resetUrl, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": getCookie("csrftoken"),
            },
            credentials: "same-origin",
            body: JSON.stringify({ email }),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            showResetMessage(data.detail || "Не удалось отправить запрос.", "error");
            return;
          }
          showResetMessage(data.detail || "Запрос отправлен администратору.", "success");
          resetForm.reset();
          window.setTimeout(() => {
            closeModal(resetModal);
          }, 400);
        } catch (error) {
          showResetMessage("Ошибка сети. Попробуйте позже.", "error");
        }
      });
    }
  }

  const initEmployeeDetail = () => {
    const detailRoot = document.querySelector("[data-employee-detail]");
    if (!detailRoot) {
      return;
    }

    const detailScope = detailRoot.closest(".content") || document;
    const updateUrl = detailRoot.dataset.employeeUpdateUrl || "";
    const positionsData = document.getElementById("department-positions-data");
    let departmentPositions = {};
    if (positionsData) {
      try {
        departmentPositions = JSON.parse(positionsData.textContent);
      } catch (error) {
        departmentPositions = {};
      }
    }
    const departmentSelect = detailScope.querySelector("[data-department-select]");
    const positionSelect = detailScope.querySelector("[data-position-select]");
    const employeeRole = detailRoot.dataset.employeeRole || "";

    const avatarInput = detailRoot.querySelector("[data-avatar-input]");
    const avatarImage = detailRoot.querySelector("[data-avatar-image]");
    const avatarFallback = detailRoot.querySelector("[data-avatar-fallback]");
    let avatarPreviewUrl = null;
    const avatarUploadUrl = detailRoot.dataset.avatarUrl;

    if (avatarInput && avatarImage) {
      if (avatarFallback) {
        avatarImage.addEventListener("error", () => {
          avatarImage.hidden = true;
          avatarFallback.hidden = false;
        });
      }
      avatarInput.addEventListener("change", (event) => {
        const [file] = event.target.files || [];
        if (!file || !file.type.startsWith("image/")) {
          return;
        }
        if (avatarPreviewUrl) {
          URL.revokeObjectURL(avatarPreviewUrl);
        }
        avatarPreviewUrl = URL.createObjectURL(file);
        avatarImage.src = avatarPreviewUrl;
        avatarImage.hidden = false;
        if (avatarFallback) {
          avatarFallback.hidden = true;
        }

        if (avatarUploadUrl) {
          const formData = new FormData();
          formData.append("avatar", file);
          fetch(avatarUploadUrl, {
            method: "POST",
            headers: {
              "X-CSRFToken": getCookie("csrftoken"),
            },
            credentials: "same-origin",
            body: formData,
          })
            .then(async (response) => {
              const data = await response.json().catch(() => ({}));
              if (!response.ok) {
                throw new Error(data.detail || "Не удалось сохранить аватар.");
              }
              if (data.avatar_url) {
                avatarImage.src = data.avatar_url;
              }
            })
            .catch((error) => {
              console.error(error);
              alert("Не удалось сохранить аватар. Попробуйте еще раз.");
            });
        }
      });
    }

    const backLink = detailRoot.querySelector(".back-link");
    const debugEnabled =
      detailRoot.dataset.debug === "true" ||
      (typeof window !== "undefined" && window.location.search.includes("debug=1"));
    const debugLog = (...args) => {
      if (debugEnabled) {
        console.log("[employee-detail]", ...args);
      }
    };
    const debugWarn = (...args) => {
      if (debugEnabled) {
        console.warn("[employee-detail]", ...args);
      }
    };
    const debugError = (...args) => {
      if (debugEnabled) {
        console.error("[employee-detail]", ...args);
      }
    };
    const unsavedModal = document.querySelector("[data-modal='unsaved-changes']");
    const unsavedConfirmButton = unsavedModal ? unsavedModal.querySelector("[data-unsaved-confirm]") : null;
    const statusDot = detailRoot.querySelector("[data-status-dot]");
    let pendingNavigation = "";

    const emailRegex = /^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$/;

    const normalizeDigits = (value) => String(value || "").replace(/\D/g, "");

    const normalizePhoneDigits = (value) => {
      let digits = normalizeDigits(value);
      if (!digits) {
        return "";
      }
      if (digits.startsWith("8")) {
        digits = `7${digits.slice(1)}`;
      }
      if (digits.startsWith("9")) {
        digits = `7${digits}`;
      }
      if (digits.length === 10 && !digits.startsWith("7")) {
        digits = `7${digits}`;
      }
      return digits;
    };

    const isValidPhone = (value) => {
      const digits = normalizePhoneDigits(value);
      return digits.length === 11 && digits.startsWith("7");
    };

    const formatPhone = (value) => {
      const digits = normalizePhoneDigits(value);
      if (!digits) {
        return "";
      }
      const normalized = digits.slice(0, 11);
      const parts = normalized.slice(1);
      const area = parts.slice(0, 3);
      const first = parts.slice(3, 6);
      const second = parts.slice(6, 8);
      const third = parts.slice(8, 10);
      let formatted = "+7";
      if (area) {
        formatted += ` (${area}`;
      }
      if (area.length === 3) {
        formatted += ")";
      }
      if (first) {
        formatted += ` ${first}`;
      }
      if (second) {
        formatted += `-${second}`;
      }
      if (third) {
        formatted += `-${third}`;
      }
      return formatted;
    };

    const formatSnils = (value) => {
      const digits = normalizeDigits(value).slice(0, 11);
      if (!digits) {
        return "";
      }
      const part1 = digits.slice(0, 3);
      const part2 = digits.slice(3, 6);
      const part3 = digits.slice(6, 9);
      const part4 = digits.slice(9, 11);
      let formatted = part1;
      if (part2) {
        formatted += `-${part2}`;
      }
      if (part3) {
        formatted += `-${part3}`;
      }
      if (part4) {
        formatted += ` ${part4}`;
      }
      return formatted;
    };

    const formatDateValue = (value) => {
      const trimmed = value.trim();
      if (!trimmed) {
        return "";
      }
      const parts = trimmed.split("-");
      if (parts.length !== 3) {
        return trimmed;
      }
      const [year, month, day] = parts;
      if (!year || !month || !day) {
        return trimmed;
      }
      return `${day}.${month}.${year}`;
    };

    const formatRateValue = (value) => {
      const normalized = value.replace(",", ".");
      const numericValue = Number(normalized);
      if (!Number.isFinite(numericValue)) {
        return "";
      }
      return `${numericValue.toFixed(2)} ₽/ч`;
    };

    const formatStatusLabel = (value) => (value === "inactive" ? "Неактивен" : "Активен");

    const updateStatusDot = (value) => {
      if (!statusDot) {
        return;
      }
      statusDot.classList.remove("success", "warning");
      statusDot.classList.add(value === "inactive" ? "warning" : "success");
    };

    const updatePositionOptions = () => {
      if (!departmentSelect || !positionSelect) {
        return;
      }
      const selectedOption = departmentSelect.selectedOptions[0];
      const departmentName =
        (selectedOption && selectedOption.dataset.name) || (selectedOption ? selectedOption.textContent : "");
      const positions = Array.isArray(departmentPositions[departmentName])
        ? departmentPositions[departmentName]
        : [];
      const currentValue = positionSelect.value || positionSelect.dataset.current || "";
      positionSelect.innerHTML = "";
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Выберите должность";
      positionSelect.append(placeholder);
      positions.forEach((position) => {
        const option = document.createElement("option");
        option.value = position;
        option.textContent = position;
        positionSelect.append(option);
      });
      if (positions.includes(currentValue)) {
        positionSelect.value = currentValue;
      } else if (currentValue) {
        const fallback = document.createElement("option");
        fallback.value = currentValue;
        fallback.textContent = currentValue;
        fallback.selected = true;
        positionSelect.append(fallback);
      }
      positionSelect.dataset.current = positionSelect.value;
      if (employeeRole === "manager") {
        const managerLabel = "Менеджер";
        if (!Array.from(positionSelect.options).some((option) => option.value === managerLabel)) {
          const managerOption = document.createElement("option");
          managerOption.value = managerLabel;
          managerOption.textContent = managerLabel;
          positionSelect.append(managerOption);
        }
        positionSelect.value = managerLabel;
        positionSelect.disabled = true;
        positionSelect.required = false;
      } else {
        positionSelect.disabled = positions.length === 0;
        positionSelect.required = positions.length > 0;
      }
      refreshCustomSelect(positionSelect.closest("[data-custom-select]"));
    };

    const getFieldType = (row) => row?.dataset.format || row?.dataset.validate || "";

    const formatInputValue = (type, value) => {
      switch (type) {
        case "email":
          return String(value || "")
            .replace(/[^A-Za-z0-9._%+-@]/g, "")
            .toLowerCase();
        case "phone":
          return formatPhone(value);
        case "passport-series":
          return normalizeDigits(value).slice(0, 4);
        case "passport-number":
          return normalizeDigits(value).slice(0, 6);
        case "snils":
          return formatSnils(value);
        case "inn":
          return normalizeDigits(value).slice(0, 12);
        default:
          return value;
      }
    };

    const formatDisplayValue = (type, rawValue) => {
      const value = String(rawValue || "").trim();
      if (!value) {
        return "—";
      }
      switch (type) {
        case "rate":
          return formatRateValue(value) || "—";
        case "phone":
          return formatPhone(value) || "—";
        case "snils":
          return formatSnils(value) || "—";
        case "inn":
        case "passport-series":
        case "passport-number": {
          const digits = normalizeDigits(value);
          return digits || "—";
        }
        case "date": {
          const formatted = formatDateValue(value);
          return formatted || "—";
        }
        case "status":
          return formatStatusLabel(value);
        default:
          return value;
      }
    };

    const validateFieldValue = (type, rawValue) => {
      const value = String(rawValue || "").trim();
      if (!value) {
        return "";
      }
      switch (type) {
        case "email":
          return emailRegex.test(value) ? "" : "Email должен быть в формате name@example.com.";
        case "phone":
          return isValidPhone(value) ? "" : "Телефон должен быть в формате +7 (900) 000-00-00.";
        case "passport-series": {
          const digits = normalizeDigits(value);
          return digits.length === 4 ? "" : "Серия паспорта должна содержать 4 цифры.";
        }
        case "passport-number": {
          const digits = normalizeDigits(value);
          return digits.length === 6 ? "" : "Номер паспорта должен содержать 6 цифр.";
        }
        case "snils": {
          const digits = normalizeDigits(value);
          return digits.length === 11 ? "" : "СНИЛС должен содержать 11 цифр.";
        }
        case "inn": {
          const digits = normalizeDigits(value);
          return digits.length === 10 || digits.length === 12
            ? ""
            : "ИНН должен содержать 10 или 12 цифр.";
        }
        case "rate": {
          const normalized = value.replace(",", ".");
          const [intPart] = normalized.split(".");
          if (intPart && intPart.replace(/\D/g, "").length > 4) {
            return "Ставка не должна содержать больше 4 цифр.";
          }
          const rateNumber = Number(normalized);
          if (Number.isNaN(rateNumber)) {
            return "Некорректная ставка.";
          }
          if (rateNumber > 9999) {
            return "Ставка не должна быть больше 9999.";
          }
          return "";
        }
        default:
          return "";
      }
    };

    if (departmentSelect) {
      departmentSelect.addEventListener("change", () => {
        updatePositionOptions();
      });
      updatePositionOptions();
    }

    const parseInputDate = (value) => {
      const trimmed = String(value || "").trim();
      if (!trimmed) {
        return null;
      }
      const dotMatch = trimmed.match(/^(\d{2})\.(\d{2})\.(\d{4})$/);
      if (dotMatch) {
        const [, day, month, year] = dotMatch;
        return new Date(Number(year), Number(month) - 1, Number(day));
      }
      const dashMatch = trimmed.match(/^(\d{4})-(\d{2})-(\d{2})$/);
      if (dashMatch) {
        const [, year, month, day] = dashMatch;
        return new Date(Number(year), Number(month) - 1, Number(day));
      }
      return null;
    };

    const formatDisplayDate = (date) => {
      if (!(date instanceof Date) || Number.isNaN(date.getTime())) {
        return "";
      }
      const day = String(date.getDate()).padStart(2, "0");
      const month = String(date.getMonth() + 1).padStart(2, "0");
      const year = date.getFullYear();
      return `${day}.${month}.${year}`;
    };

    const initDatePickers = () => {
      const pickers = Array.from(detailScope.querySelectorAll("[data-date-picker]"));
      if (!pickers.length) {
        return;
      }
      const monthNames = [
        "Январь",
        "Февраль",
        "Март",
        "Апрель",
        "Май",
        "Июнь",
        "Июль",
        "Август",
        "Сентябрь",
        "Октябрь",
        "Ноябрь",
        "Декабрь",
      ];
      const weekdayLabels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
      const openPanels = new Set();

      const renderCalendar = (panel, state) => {
        panel.innerHTML = "";
        const header = document.createElement("div");
        header.className = "date-panel-header";
        const prev = document.createElement("button");
        prev.type = "button";
        prev.textContent = "‹";
        const next = document.createElement("button");
        next.type = "button";
        next.textContent = "›";
        const label = document.createElement("div");
        label.textContent = `${monthNames[state.viewDate.getMonth()]} ${state.viewDate.getFullYear()}`;
        header.append(prev, label, next);
        panel.append(header);

        const grid = document.createElement("div");
        grid.className = "date-panel-grid";
        weekdayLabels.forEach((dayLabel) => {
          const cell = document.createElement("div");
          cell.className = "date-panel-weekday";
          cell.textContent = dayLabel;
          grid.append(cell);
        });

        const year = state.viewDate.getFullYear();
        const month = state.viewDate.getMonth();
        const firstDay = new Date(year, month, 1);
        const startOffset = (firstDay.getDay() + 6) % 7;
        const daysInMonth = new Date(year, month + 1, 0).getDate();
        const daysInPrev = new Date(year, month, 0).getDate();

        for (let i = 0; i < 42; i += 1) {
          const dayIndex = i - startOffset + 1;
          const cell = document.createElement("div");
          cell.className = "date-panel-day";
          let cellDate;
          if (dayIndex <= 0) {
            const day = daysInPrev + dayIndex;
            cellDate = new Date(year, month - 1, day);
            cell.classList.add("is-muted");
          } else if (dayIndex > daysInMonth) {
            const day = dayIndex - daysInMonth;
            cellDate = new Date(year, month + 1, day);
            cell.classList.add("is-muted");
          } else {
            cellDate = new Date(year, month, dayIndex);
          }
          cell.textContent = String(cellDate.getDate());
          if (
            state.selectedDate &&
            cellDate.toDateString() === state.selectedDate.toDateString()
          ) {
            cell.classList.add("is-selected");
          }
          cell.addEventListener("click", () => {
            state.selectedDate = cellDate;
            state.input.value = formatDisplayDate(cellDate);
            state.panel.hidden = true;
            openPanels.delete(state.panel);
            state.input.dispatchEvent(new Event("input", { bubbles: true }));
          });
          grid.append(cell);
        }

        panel.append(grid);

        prev.addEventListener("click", () => {
          state.viewDate = new Date(year, month - 1, 1);
          renderCalendar(panel, state);
        });

        next.addEventListener("click", () => {
          state.viewDate = new Date(year, month + 1, 1);
          renderCalendar(panel, state);
        });
      };

      pickers.forEach((picker) => {
        if (picker.dataset.dateReady === "true") {
          return;
        }
        picker.dataset.dateReady = "true";
        const input = picker.querySelector("[data-date-input]");
        const panel = picker.querySelector("[data-date-panel]");
        const trigger = picker.querySelector("[data-date-trigger]");
        if (!input || !panel || !trigger) {
          return;
        }
        const initialDate = parseInputDate(input.value) || new Date();
        const state = {
          input,
          panel,
          viewDate: new Date(initialDate.getFullYear(), initialDate.getMonth(), 1),
          selectedDate: parseInputDate(input.value),
        };
        const openPanel = () => {
          if (input.disabled) {
            return;
          }
          state.selectedDate = parseInputDate(input.value);
          state.viewDate = state.selectedDate
            ? new Date(state.selectedDate.getFullYear(), state.selectedDate.getMonth(), 1)
            : new Date();
          panel.hidden = false;
          openPanels.add(panel);
          renderCalendar(panel, state);
        };
        trigger.addEventListener("click", (event) => {
          event.stopPropagation();
          openPanel();
        });
        input.addEventListener("focus", () => {
          openPanel();
        });
      });

      document.addEventListener("click", (event) => {
        pickers.forEach((picker) => {
          const panel = picker.querySelector("[data-date-panel]");
          if (!panel) {
            return;
          }
          if (!picker.contains(event.target)) {
            panel.hidden = true;
            openPanels.delete(panel);
          }
        });
      });
    };

    initDatePickers();

    const formatApiErrors = (data) => {
      if (!data || typeof data !== "object") {
        return "";
      }
      if (data.detail) {
        return data.detail;
      }
      const parts = [];
      Object.entries(data).forEach(([key, value]) => {
        if (!value) {
          return;
        }
        const message = Array.isArray(value) ? value.join(" ") : String(value);
        parts.push(`${message}`);
      });
      return parts.join("\n");
    };

    const buildSectionPayload = (inputs, options = {}) => {
      const payload = {};
      inputs.forEach((input) => {
        if (!input.name) {
          return;
        }
        const isHourlyReason = input.name === "hourly_rate_reason" && options.includeRateReason;
        const isDeptReason = input.name === "department_change_reason" && options.includeDepartmentReason;
        if (
          !isHourlyReason &&
          !isDeptReason &&
          Object.prototype.hasOwnProperty.call(input.dataset, "originalValue") &&
          input.value === input.dataset.originalValue
        ) {
          return;
        }
        const value = input.value;
        if (input.name === "is_active") {
          payload.is_active = value === "active";
          return;
        }
        if (input.name === "department_id") {
          payload.department_id = value ? Number(value) : null;
          return;
        }
        if (input.name === "hourly_rate") {
          payload.hourly_rate = value ? Number(value.replace(",", ".")) : null;
          return;
        }
        if (input.name === "passport_issue_date") {
          payload.passport_issue_date = value ? value : null;
          return;
        }
        payload[input.name] = value;
      });
      if (!options.includeRateReason && "hourly_rate_reason" in payload) {
        delete payload.hourly_rate_reason;
      }
      if (employeeRole === "manager" && "position" in payload) {
        delete payload.position;
      }
      return payload;
    };

    const applyInputsToSection = (fieldRows) => {
      fieldRows.forEach((row) => {
        const input = row.querySelector("[data-field-input]");
        const valueNode = row.querySelector("[data-field-value]");
        if (!input || !valueNode) {
          return;
        }
        const fieldType = getFieldType(row);
        let displayValue = formatDisplayValue(fieldType, input.value);
        if (fieldType === "department" || fieldType === "position") {
          const selectedOption = input.selectedOptions ? input.selectedOptions[0] : null;
          displayValue = selectedOption ? selectedOption.textContent : displayValue;
        }
        valueNode.textContent = displayValue || "—";
        input.dataset.originalValue = input.value;
      });
    };

    const applyResponseToSection = (section, fieldRows, data) => {
      if (!data) {
        return;
      }
      fieldRows.forEach((row) => {
        const input = row.querySelector("[data-field-input]");
        const valueNode = row.querySelector("[data-field-value]");
        if (!input || !valueNode) {
          return;
        }
        if (input.name === "first_name" && Object.prototype.hasOwnProperty.call(data, "first_name")) {
          input.value = data.first_name || "";
        } else if (input.name === "last_name" && Object.prototype.hasOwnProperty.call(data, "last_name")) {
          input.value = data.last_name || "";
        } else if (input.name === "middle_name" && Object.prototype.hasOwnProperty.call(data, "middle_name")) {
          input.value = data.middle_name || "";
        } else if (input.name === "department_id" && Object.prototype.hasOwnProperty.call(data, "department_id")) {
          input.value = data.department_id ? String(data.department_id) : "";
          refreshCustomSelect(input.closest("[data-custom-select]"));
          updatePositionOptions();
        } else if (input.name === "position" && Object.prototype.hasOwnProperty.call(data, "position")) {
          input.value = data.position || "";
          input.dataset.current = input.value;
          refreshCustomSelect(input.closest("[data-custom-select]"));
        } else if (input.name === "is_active" && Object.prototype.hasOwnProperty.call(data, "is_active")) {
          input.value = data.is_active ? "active" : "inactive";
          updateStatusDot(input.value);
          detailRoot.dataset.employeeActive = input.value === "inactive" ? "false" : "true";
          refreshCustomSelect(input.closest("[data-custom-select]"));
        } else if (input.name === "hourly_rate" && Object.prototype.hasOwnProperty.call(data, "hourly_rate")) {
          input.value = data.hourly_rate !== null && data.hourly_rate !== undefined ? String(data.hourly_rate) : "";
        } else if (
          input.name === "hourly_rate_reason" &&
          Object.prototype.hasOwnProperty.call(data, "hourly_rate_reason")
        ) {
          input.value = data.hourly_rate_reason || "";
        } else if (input.name === "email" && Object.prototype.hasOwnProperty.call(data, "email")) {
          input.value = data.email || "";
        } else if (
          input.name === "corporate_phone" &&
          Object.prototype.hasOwnProperty.call(data, "corporate_phone")
        ) {
          input.value = data.corporate_phone || "";
        } else if (
          input.name === "personal_phone" &&
          Object.prototype.hasOwnProperty.call(data, "personal_phone")
        ) {
          input.value = data.personal_phone || "";
        } else if (input.name === "address" && Object.prototype.hasOwnProperty.call(data, "address")) {
          input.value = data.address || "";
        } else if (
          input.name === "passport_series" &&
          Object.prototype.hasOwnProperty.call(data, "passport_series")
        ) {
          input.value = data.passport_series || "";
        } else if (
          input.name === "passport_number" &&
          Object.prototype.hasOwnProperty.call(data, "passport_number")
        ) {
          input.value = data.passport_number || "";
        } else if (
          input.name === "passport_issued_by" &&
          Object.prototype.hasOwnProperty.call(data, "passport_issued_by")
        ) {
          input.value = data.passport_issued_by || "";
        } else if (
          input.name === "passport_issue_date" &&
          Object.prototype.hasOwnProperty.call(data, "passport_issue_date")
        ) {
          input.value = formatDateValue(data.passport_issue_date || "");
        } else if (input.name === "snils" && Object.prototype.hasOwnProperty.call(data, "snils")) {
          input.value = data.snils || "";
        } else if (input.name === "inn" && Object.prototype.hasOwnProperty.call(data, "inn")) {
          input.value = data.inn || "";
        }

        let displayValue = "";
        if (row.dataset.format === "department" && Object.prototype.hasOwnProperty.call(data, "department")) {
          displayValue = data.department || "—";
        } else if (row.dataset.format === "position" && Object.prototype.hasOwnProperty.call(data, "position")) {
          displayValue = data.position || "—";
        } else if (row.dataset.format === "status" && Object.prototype.hasOwnProperty.call(data, "is_active")) {
          displayValue = data.is_active ? "Активен" : "Неактивен";
        } else if (row.dataset.format === "rate" && Object.prototype.hasOwnProperty.call(data, "hourly_rate")) {
          displayValue =
            data.hourly_rate !== null && data.hourly_rate !== undefined
              ? formatRateValue(String(data.hourly_rate))
              : "—";
        } else {
          displayValue = formatDisplayValue(getFieldType(row), input.value);
        }
        valueNode.textContent = displayValue;
        input.dataset.originalValue = input.value;
      });

      const chips = detailRoot.querySelectorAll(".profile-chips .info-chip");
      if (chips.length >= 2) {
        chips[0].textContent = data.email || "—";
        chips[1].textContent = data.corporate_phone || "—";
      }
      const heroTitle = detailRoot.querySelector("h1");
      if (heroTitle && Object.prototype.hasOwnProperty.call(data, "full_name")) {
        heroTitle.textContent = data.full_name || heroTitle.textContent;
      }
      const heroMeta = detailRoot.querySelector(".profile-hero-text .subtle");
      if (heroMeta && Object.prototype.hasOwnProperty.call(data, "department")) {
        const roleText = heroMeta.textContent.split("·")[0].trim();
        heroMeta.textContent = `${roleText} · ${data.department || "—"}`;
      }
    };
    const sections = Array.from(detailScope.querySelectorAll("[data-inline-edit]"));
    const updateGlobalUnsaved = () => {
      const hasUnsaved = sections.some((section) => section.dataset.isDirty === "true");
      detailRoot.dataset.hasUnsaved = hasUnsaved ? "true" : "false";
    };

    sections.forEach((section) => {
      const toggleButton = section.querySelector("[data-edit-toggle]");
      const saveButton = section.querySelector("[data-edit-save]");
      const cancelButton = section.querySelector("[data-edit-cancel]");
      if (!toggleButton || !saveButton || !cancelButton) {
        return;
      }

      const sectionMessage = section.querySelector("[data-section-message]");
      const fieldRows = Array.from(section.querySelectorAll("[data-field-row]"));
      const inputs = fieldRows
        .map((row) => row.querySelector("[data-field-input]"))
        .filter(Boolean);
      const rateInput = section.querySelector("[name='hourly_rate']");
      const rateReasonRow = section.querySelector("[data-rate-reason-row]");
      const rateReasonInput = section.querySelector("[name='hourly_rate_reason']");

      const showSectionMessage = (text, type) => {
        if (!sectionMessage) {
          return;
        }
        sectionMessage.textContent = text || "";
        sectionMessage.classList.remove("is-error", "is-success");
        if (!text) {
          sectionMessage.hidden = true;
          return;
        }
        sectionMessage.hidden = false;
        if (type) {
          sectionMessage.classList.add(type === "error" ? "is-error" : "is-success");
        }
      };

      const clearValidation = () => {
        inputs.forEach((input) => {
          input.classList.remove("is-invalid");
          const wrapper = input.closest(".info-input");
          if (wrapper && wrapper !== input) {
            wrapper.classList.remove("is-invalid");
          }
        });
        showSectionMessage("", null);
      };

      const setEditingState = (isEditing) => {
        section.classList.toggle("is-editing", isEditing);
        toggleButton.hidden = isEditing;
        saveButton.hidden = !isEditing;
        cancelButton.hidden = !isEditing;
        inputs.forEach((input) => {
          input.disabled = !isEditing;
          if (input.tagName === "SELECT") {
            refreshCustomSelect(input.closest("[data-custom-select]"));
          }
        });
        if (rateReasonRow) {
          if (isEditing) {
            updateRateReasonState();
          } else {
            rateReasonRow.classList.remove("is-hidden");
          }
        }
      };

      const updateSectionDirty = () => {
        const hasChanges = inputs.some(
          (input) =>
            input.dataset.originalValue !== undefined && input.value !== input.dataset.originalValue
        );
        if (hasChanges) {
          section.dataset.isDirty = "true";
        } else {
          delete section.dataset.isDirty;
        }
        updateGlobalUnsaved();
      };

      const updateRateReasonState = () => {
        if (!rateInput || !rateReasonRow || !rateReasonInput) {
          return;
        }
        if (!section.classList.contains("is-editing")) {
          rateReasonRow.classList.remove("is-hidden");
          return;
        }
        const originalValue = Number(
          String(rateInput.dataset.originalValue || "").replace(",", "."),
        );
        const currentValue = Number(String(rateInput.value || "").replace(",", "."));
        const hasIncrease =
          Number.isFinite(currentValue) &&
          Number.isFinite(originalValue) &&
          currentValue > originalValue;
        if (hasIncrease) {
          rateReasonRow.classList.remove("is-hidden");
        } else {
          rateReasonRow.classList.add("is-hidden");
        }
      };

      inputs.forEach((input) => {
        const row = input.closest("[data-field-row]");
        const fieldType = getFieldType(row);
        const handleInput = () => {
          if (fieldType) {
            const formatted = formatInputValue(fieldType, input.value);
            if (formatted !== input.value) {
              input.value = formatted;
            }
          }
          if (input.classList.contains("is-invalid")) {
            input.classList.remove("is-invalid");
            const wrapper = input.closest(".info-input");
            if (wrapper && wrapper !== input) {
              wrapper.classList.remove("is-invalid");
            }
          }
          if (input === positionSelect) {
            input.dataset.current = input.value;
          }
          if (input === rateInput) {
            updateRateReasonState();
          }
          updateSectionDirty();
        };
        input.addEventListener("input", handleInput);
        input.addEventListener("change", handleInput);
      });

      toggleButton.addEventListener("click", () => {
        clearValidation();
        fieldRows.forEach((row) => {
          const input = row.querySelector("[data-field-input]");
          if (!input) {
            return;
          }
          const fieldType = getFieldType(row);
          if (fieldType) {
            input.value = formatInputValue(fieldType, input.value);
          }
          input.dataset.originalValue = input.value;
        });
        setEditingState(true);
        updateRateReasonState();
        updateSectionDirty();
      });

      cancelButton.addEventListener("click", () => {
        fieldRows.forEach((row) => {
          const input = row.querySelector("[data-field-input]");
          if (!input) {
            return;
          }
          if (input.dataset.originalValue !== undefined) {
            input.value = input.dataset.originalValue;
          }
        });
        clearValidation();
        setEditingState(false);
        updateRateReasonState();
        updateSectionDirty();
      });

      saveButton.addEventListener("click", async () => {
        clearValidation();
        const errorMessages = new Set();
        fieldRows.forEach((row) => {
          const input = row.querySelector("[data-field-input]");
          if (!input) {
            return;
          }
          const fieldType = row.dataset.validate || "";
          const error = fieldType ? validateFieldValue(fieldType, input.value) : "";
          if (error) {
            errorMessages.add(error);
            input.classList.add("is-invalid");
            const wrapper = input.closest(".info-input");
            if (wrapper && wrapper !== input) {
              wrapper.classList.add("is-invalid");
            }
          }
        });

        let includeRateReason = false;
        if (rateInput) {
          const originalRate = Number(
            String(rateInput.dataset.originalValue || "").replace(",", "."),
          );
          const currentRaw = String(rateInput.value || "").trim();
          const currentRate = currentRaw ? Number(currentRaw.replace(",", ".")) : null;
          if (currentRate === null && originalRate > 0) {
            errorMessages.add("Ставку можно только повысить.");
            rateInput.classList.add("is-invalid");
            const wrapper = rateInput.closest(".info-input");
            if (wrapper && wrapper !== rateInput) {
              wrapper.classList.add("is-invalid");
            }
          } else if (
            currentRate !== null &&
            Number.isFinite(originalRate) &&
            Number.isFinite(currentRate) &&
            currentRate < originalRate
          ) {
            errorMessages.add("Ставку можно только повысить.");
            rateInput.classList.add("is-invalid");
            const wrapper = rateInput.closest(".info-input");
            if (wrapper && wrapper !== rateInput) {
              wrapper.classList.add("is-invalid");
            }
          } else if (
            currentRate !== null &&
            Number.isFinite(originalRate) &&
            Number.isFinite(currentRate) &&
            currentRate > originalRate
          ) {
            includeRateReason = true;
            if (rateReasonInput && !rateReasonInput.value.trim()) {
              errorMessages.add("Укажите причину повышения ставки.");
              rateReasonInput.classList.add("is-invalid");
              const wrapper = rateReasonInput.closest(".info-input");
              if (wrapper && wrapper !== rateReasonInput) {
                wrapper.classList.add("is-invalid");
              }
            }
          }
        }

        if (errorMessages.size) {
          showSectionMessage(Array.from(errorMessages).join("\n"), "error");
          return;
        }

        let responseData = null;
        const departmentInput = inputs.find((input) => input.name === "department_id");
        const includeDepartmentReason =
          departmentInput &&
          Object.prototype.hasOwnProperty.call(departmentInput.dataset, "originalValue") &&
          departmentInput.value !== departmentInput.dataset.originalValue;
        const payload = buildSectionPayload(inputs, {
          includeRateReason,
          includeDepartmentReason,
        });
        if (updateUrl) {
          debugLog("Saving section", {
            sectionTitle: section.querySelector("h3")?.textContent,
            updateUrl,
            payload,
          });
          if (Object.keys(payload).length) {
            saveButton.disabled = true;
            try {
              const response = await fetch(updateUrl, {
                method: "PATCH",
                headers: {
                  "Content-Type": "application/json",
                  "X-CSRFToken": getCookie("csrftoken"),
                },
                credentials: "same-origin",
                body: JSON.stringify(payload),
              });
              const data = await response.json().catch(() => ({}));
              debugLog("API response", {
                status: response.status,
                ok: response.ok,
                data,
              });
              if (!response.ok) {
                showSectionMessage(
                  formatApiErrors(data) || "Не удалось сохранить изменения.",
                  "error",
                );
                debugWarn("Save failed", data);
                setEditingState(true);
                updateSectionDirty();
                saveButton.disabled = false;
                return;
              }
              responseData = data;
            } catch (error) {
              showSectionMessage("Ошибка сети. Попробуйте позже.", "error");
              debugError("Save request failed", error);
              setEditingState(true);
              updateSectionDirty();
              saveButton.disabled = false;
              return;
            }
            saveButton.disabled = false;
          }
        } else {
          debugWarn("Update URL is missing, skipping API save.");
        }

        if (responseData && Object.keys(responseData).length) {
          applyResponseToSection(section, fieldRows, responseData);
          applyInputsToSection(fieldRows);
          debugLog("Applied API response to section", {
            sectionTitle: section.querySelector("h3")?.textContent,
            responseData,
          });
        } else {
          applyInputsToSection(fieldRows);
          if (section.querySelector("[name='is_active']")) {
            const statusInput = section.querySelector("[name='is_active']");
            updateStatusDot(statusInput.value);
            detailRoot.dataset.employeeActive = statusInput.value === "inactive" ? "false" : "true";
          }
        }
        setEditingState(false);
        updateSectionDirty();
      });

      setEditingState(false);
      applyInputsToSection(fieldRows);
    });

    if (backLink && unsavedModal) {
      backLink.addEventListener("click", (event) => {
        if (detailRoot.dataset.hasUnsaved !== "true") {
          return;
        }
        event.preventDefault();
        pendingNavigation = backLink.getAttribute("href") || "";
        openModal(unsavedModal);
      });
    }

    if (unsavedConfirmButton) {
      unsavedConfirmButton.addEventListener("click", () => {
        if (unsavedModal) {
          closeModal(unsavedModal);
        }
        if (pendingNavigation) {
          window.location.href = pendingNavigation;
        } else {
          window.history.back();
        }
      });
    }
  };

  initEmployeeDetail();
  const initDepartmentDetail = () => {
    const detailRoot = document.querySelector("[data-department-detail]");
    if (!detailRoot) {
      return;
    }

    const detailScope = detailRoot.closest(".content") || document;
    const departmentId = detailRoot.dataset.departmentId || "";
    const employeeUpdateBase = detailRoot.dataset.employeeUpdateBase || "";
    const employeeDetailBase = detailRoot.dataset.employeeDetailBase || "";
    const departmentUpdateUrl = detailRoot.dataset.departmentUpdateUrl || "";
    const positionsSeed = document.getElementById("department-positions-values");
    const section = detailScope.querySelector("[data-department-employees]");
    const infoSection = detailScope.querySelector("[data-department-info]");
    if (!section) {
      return;
    }
    const toggleButton = section.querySelector("[data-dept-edit-toggle]");
    const doneButton = section.querySelector("[data-dept-edit-done]");
    const addBlock = section.querySelector("[data-employee-add]");
    const addForm = section.querySelector("[data-employee-add-form]");
    const message = section.querySelector("[data-section-message]");
    const listBody = section.querySelector("[data-employee-list]");
    const employeeCount = detailRoot.querySelector("[data-employee-count]");
    const transferModal = document.querySelector("[data-modal='transfer-employee']");
    const transferForm = transferModal ? transferModal.querySelector("[data-transfer-form]") : null;
    const transferMessage = transferModal ? transferModal.querySelector("[data-transfer-message]") : null;
    const transferEmployeeName = transferModal ? transferModal.querySelector("[data-transfer-employee-name]") : null;
    const transferDepartmentSelect = transferForm ? transferForm.querySelector("[name='department_id']") : null;
    const transferReasonInput = transferForm ? transferForm.querySelector("[name='department_change_reason']") : null;
    let transferTarget = null;

    if (!toggleButton || !doneButton || !addBlock || !addForm) {
      return;
    }

    const employeeSelect = addForm.querySelector("[name='employee_id']");
    const positionInput = addForm.querySelector("[name='position']");
    const rateInput = addForm.querySelector("[name='hourly_rate']");
    const positionSelect = addForm.querySelector("[data-position-select]");

    if (infoSection && positionsSeed) {
      const tagStorage = infoSection.querySelector("[data-tag-storage]");
      if (tagStorage) {
        tagStorage.value = positionsSeed.textContent || "[]";
      }
    }

    const buildEmployeeUpdateUrl = (id) => {
      if (!employeeUpdateBase) {
        return "";
      }
      return employeeUpdateBase.replace(/0\/?$/, `${id}/`);
    };

    const buildEmployeeDetailUrl = (id) => {
      if (!employeeDetailBase) {
        return `/dashboard/admin/users/${id}/`;
      }
      return employeeDetailBase.replace(/0\/?$/, `${id}/`);
    };

    const showMessage = (text, type) => {
      if (!message) {
        return;
      }
      message.textContent = text || "";
      message.classList.remove("is-error", "is-success");
      if (!text) {
        message.hidden = true;
        return;
      }
      message.hidden = false;
      if (type) {
        message.classList.add(type === "error" ? "is-error" : "is-success");
      }
    };

    const getEmptyRow = () => (listBody ? listBody.querySelector("[data-empty-row]") : null);

    const ensureEmptyRow = () => {
      if (!listBody) {
        return null;
      }
      let row = getEmptyRow();
      if (!row) {
        row = document.createElement("tr");
        row.dataset.emptyRow = "true";
        row.innerHTML = '<td colspan="7" class="subtle">В отделе пока нет сотрудников.</td>';
        listBody.append(row);
      }
      return row;
    };

    const addEmployeeOption = (employee) => {
      if (!employeeSelect || !employee) {
        return;
      }
      const name = employee.full_name || employee.username || employee.name || "Сотрудник";
      const email = employee.email || "";
      const option = document.createElement("option");
      option.value = String(employee.id);
      option.textContent = email ? `${name} · ${email}` : name;
      employeeSelect.append(option);
      employeeSelect.disabled = false;
      refreshCustomSelect(employeeSelect.closest("[data-custom-select]"));
    };

    const updateEmployeeCount = (delta) => {
      if (!employeeCount) {
        return;
      }
      const current = Number(employeeCount.textContent || "0");
      const next = Math.max(0, current + delta);
      employeeCount.textContent = String(next);
    };

    const setEditingState = (isEditing) => {
      section.classList.toggle("is-editing", isEditing);
      addBlock.hidden = !isEditing;
      toggleButton.hidden = isEditing;
      doneButton.hidden = !isEditing;
      addForm.querySelectorAll("input, select, button").forEach((input) => {
        input.disabled = !isEditing;
      });
      if (isEditing && employeeSelect) {
        const hasOptions = employeeSelect.options.length > 1;
        employeeSelect.disabled = !hasOptions;
      }
      if (isEditing) {
        const submitButton = addForm.querySelector("button[type='submit']");
        if (submitButton && employeeSelect && employeeSelect.disabled) {
          submitButton.disabled = true;
        }
      }
      if (employeeSelect) {
        refreshCustomSelect(employeeSelect.closest("[data-custom-select]"));
      }
      if (positionInput && positionInput.tagName === "SELECT") {
        refreshCustomSelect(positionInput.closest("[data-custom-select]"));
      }
    };

    const formatRate = (value) => {
      if (value === null || value === undefined || value === "") {
        return "—";
      }
      const numberValue = Number(value);
      if (Number.isNaN(numberValue)) {
        return value;
      }
      const formatter = new Intl.NumberFormat("ru-RU", {
        style: "currency",
        currency: "RUB",
        maximumFractionDigits: 0,
      });
      return `${formatter.format(numberValue)}/ч`;
    };

    const validateRate = (value) => {
      if (!value) {
        return "";
      }
      const normalized = value.replace(",", ".");
      const [intPart] = normalized.split(".");
      if (intPart && intPart.replace(/\D/g, "").length > 4) {
        return "Ставка не должна содержать больше 4 цифр.";
      }
      const rateNumber = Number(normalized);
      if (Number.isNaN(rateNumber)) {
        return "Некорректная ставка.";
      }
      if (rateNumber > 9999) {
        return "Ставка не должна быть больше 9999.";
      }
      return "";
    };

    const showTransferMessage = (text, type) => {
      if (!transferMessage) {
        return;
      }
      transferMessage.textContent = text || "";
      transferMessage.classList.remove("is-error", "is-success");
      if (!text) {
        transferMessage.hidden = true;
        return;
      }
      transferMessage.hidden = false;
      if (type) {
        transferMessage.classList.add(type === "error" ? "is-error" : "is-success");
      }
    };

    const openTransferModal = (row) => {
      if (!transferModal || !transferForm || !row) {
        return;
      }
      const employeeId = row.dataset.employeeId || "";
      if (!employeeId) {
        return;
      }
      const name =
        row.dataset.employeeName ||
        row.querySelector("td")?.textContent?.trim() ||
        "Сотрудник";
      transferTarget = {
        id: employeeId,
        name,
        email: row.dataset.employeeEmail || "",
        row,
      };
      if (transferEmployeeName) {
        transferEmployeeName.textContent = `Сотрудник: ${name}`;
      }
      if (transferDepartmentSelect) {
        transferDepartmentSelect.value = "";
        refreshCustomSelect(transferDepartmentSelect.closest("[data-custom-select]"));
      }
      if (transferReasonInput) {
        transferReasonInput.value = "";
      }
      showTransferMessage("", null);
      openModal(transferModal);
    };

    const renderEmployeeRow = (employee) => {
      const row = document.createElement("tr");
      const fullName = employee.full_name || employee.username || "—";
      row.dataset.employeeId = employee.id ? String(employee.id) : "";
      row.dataset.employeeName = fullName;
      row.dataset.employeeEmail = employee.email || "";
      row.dataset.active = employee.is_active ? "true" : "false";
      if (!employee.is_active) {
        row.classList.add("is-inactive");
      }
      const statusClass = employee.is_active ? "success" : "warning";
      row.innerHTML = `
        <td>${fullName}</td>
        <td>${employee.email || "—"}</td>
        <td>${employee.role_display || employee.role || "—"}</td>
        <td>${employee.position || "—"}</td>
        <td>${formatRate(employee.hourly_rate)}</td>
        <td>
          <span class="status-dot ${statusClass}" aria-hidden="true"></span>
          <span class="sr-only">${employee.status_label || ""}</span>
        </td>
        <td>
          <div class="table-actions">
            <button class="icon-action" type="button" data-employee-transfer aria-label="Перевести сотрудника">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M7 6h10m0 0-3-3m3 3-3 3M17 18H7m0 0 3 3m-3-3 3-3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
              </svg>
            </button>
            <a class="icon-action" href="${buildEmployeeDetailUrl(employee.id)}" aria-label="Карточка сотрудника">
              <svg viewBox="0 0 24 24" aria-hidden="true">
                <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
                <circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.6" />
              </svg>
            </a>
          </div>
        </td>
      `;
      return row;
    };

    const updateAddFormPositions = (positions) => {
      if (!positionSelect) {
        return;
      }
      positionSelect.innerHTML = "";
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = positions.length ? "Выберите должность" : "Должности не заданы";
      if (!positions.length) {
        placeholder.disabled = true;
      }
      positionSelect.append(placeholder);
      positions.forEach((title) => {
        const option = document.createElement("option");
        option.value = title;
        option.textContent = title;
        positionSelect.append(option);
      });
      positionSelect.disabled = positions.length === 0;
      refreshCustomSelect(positionSelect.closest("[data-custom-select]"));
    };

    const updateInfoPositions = (positions) => {
      if (!infoSection) {
        return;
      }
      const display = infoSection.querySelector("[data-display-positions]");
      if (!display) {
        return;
      }
      display.innerHTML = "";
      if (!positions.length) {
        const pill = document.createElement("span");
        pill.className = "pill";
        pill.textContent = "Должности не заданы";
        display.append(pill);
        return;
      }
      positions.forEach((title) => {
        const pill = document.createElement("span");
        pill.className = "pill";
        pill.textContent = title;
        display.append(pill);
      });
    };

    const initInfoEditing = () => {
      if (!infoSection) {
        return;
      }
      const editButton = infoSection.querySelector("[data-dept-info-edit]");
      const saveButton = infoSection.querySelector("[data-dept-info-save]");
      const cancelButton = infoSection.querySelector("[data-dept-info-cancel]");
      const messageEl = infoSection.querySelector("[data-section-message]");
      const nameInput = infoSection.querySelector("[name='name']");
      const managerSelect = infoSection.querySelector("[name='manager_id']");
      const tagStorage = infoSection.querySelector("[data-tag-storage]");
      const tagList = infoSection.querySelector("[data-tag-list]");
      const tagField = infoSection.querySelector("[data-tag-field]");
      const tagAdd = infoSection.querySelector("[data-tag-add]");
      const nameDisplay = infoSection.querySelector("[data-display-name]");
      const managerDisplay = infoSection.querySelector("[data-display-manager]");
      const heroTitle = detailRoot.querySelector("h1");
      const positionsCountChip = detailRoot.querySelector("[data-positions-count]");

      if (!editButton || !saveButton || !cancelButton || !nameInput || !tagStorage) {
        return;
      }

      const showInfoMessage = (text, type) => {
        if (!messageEl) {
          return;
        }
        messageEl.textContent = text || "";
        messageEl.classList.remove("is-error", "is-success");
        if (!text) {
          messageEl.hidden = true;
          return;
        }
        messageEl.hidden = false;
        if (type) {
          messageEl.classList.add(type === "error" ? "is-error" : "is-success");
        }
      };

      const setEditingState = (isEditing) => {
        infoSection.classList.toggle("is-editing", isEditing);
        editButton.hidden = isEditing;
        saveButton.hidden = !isEditing;
        cancelButton.hidden = !isEditing;
        [nameInput, managerSelect, tagField, tagAdd].forEach((input) => {
          if (input) {
            input.disabled = !isEditing;
          }
        });
        if (tagList) {
          tagList.querySelectorAll(".tag-remove").forEach((button) => {
            button.disabled = !isEditing;
          });
        }
        if (managerSelect) {
          refreshCustomSelect(managerSelect.closest("[data-custom-select]"));
        }
      };

      const getPositions = () => {
        if (!tagStorage.value) {
          return [];
        }
        try {
          const parsed = JSON.parse(tagStorage.value);
          return Array.isArray(parsed) ? parsed : [];
        } catch (error) {
          return [];
        }
      };

      const snapshot = () => ({
        name: nameInput.value,
        managerId: managerSelect ? managerSelect.value : "",
        positions: tagStorage.value,
        tagHtml: tagList ? tagList.innerHTML : "",
      });

      let original = snapshot();

      const applyDisplay = (payload) => {
        if (nameDisplay) {
          nameDisplay.textContent = payload.name || "—";
        }
        if (heroTitle) {
          heroTitle.textContent = payload.name || "Отдел";
        }
        if (managerDisplay) {
          managerDisplay.textContent = payload.manager_name || "—";
        }
        updateInfoPositions(payload.positions || []);
        if (positionsCountChip) {
          positionsCountChip.textContent = `${payload.positions.length} должностей`;
        }
      };

      editButton.addEventListener("click", () => {
        showInfoMessage("", null);
        original = snapshot();
        setEditingState(true);
      });

      cancelButton.addEventListener("click", () => {
        showInfoMessage("", null);
        nameInput.value = original.name;
        if (managerSelect) {
          managerSelect.value = original.managerId;
          refreshCustomSelect(managerSelect.closest("[data-custom-select]"));
        }
        if (tagStorage) {
          tagStorage.value = original.positions;
        }
        if (tagList) {
          tagList.innerHTML = original.tagHtml;
        }
        setEditingState(false);
      });

      saveButton.addEventListener("click", async () => {
        showInfoMessage("", null);
        const nameValue = nameInput.value.trim();
        if (!nameValue) {
          showInfoMessage("Укажите название отдела.", "error");
          return;
        }
        const positions = getPositions();
        const payload = {
          name: nameValue,
          positions,
        };
        if (managerSelect) {
          payload.manager_id = managerSelect.value ? Number(managerSelect.value) : null;
        }
        if (!departmentUpdateUrl) {
          showInfoMessage("URL обновления отдела не задан.", "error");
          return;
        }
        saveButton.disabled = true;
        try {
          const response = await fetch(departmentUpdateUrl, {
            method: "PATCH",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": getCookie("csrftoken"),
            },
            credentials: "same-origin",
            body: JSON.stringify(payload),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            const detail = data.detail || "Не удалось сохранить изменения.";
            showInfoMessage(detail, "error");
            saveButton.disabled = false;
            return;
          }
          if (data && typeof data === "object") {
            const updatedPositions = Array.isArray(data.positions) ? data.positions : positions;
            applyDisplay({
              name: data.name || nameValue,
              manager_name: data.manager_name || "—",
              positions: updatedPositions,
            });
            if (managerSelect && Object.prototype.hasOwnProperty.call(data, "manager_id")) {
              managerSelect.value = data.manager_id ? String(data.manager_id) : "";
              refreshCustomSelect(managerSelect.closest("[data-custom-select]"));
            }
            updateAddFormPositions(updatedPositions);
            if (tagStorage) {
              tagStorage.value = JSON.stringify(updatedPositions);
            }
            if (tagList) {
              tagList.innerHTML = "";
              updatedPositions.forEach((title) => {
                const tag = document.createElement("span");
                tag.className = "tag";
                tag.dataset.tagValue = title;
                tag.innerHTML = `
                  <span class="tag-label">${title}</span>
                  <button class="tag-remove" type="button" aria-label="Удалить">×</button>
                `;
                tagList.append(tag);
              });
            }
          }
          setEditingState(false);
          showInfoMessage("Данные отдела обновлены.", "success");
        } catch (error) {
          showInfoMessage("Ошибка сети. Попробуйте позже.", "error");
        } finally {
          saveButton.disabled = false;
        }
      });

      setEditingState(false);
    };

    toggleButton.addEventListener("click", () => {
      showMessage("", null);
      setEditingState(true);
    });

    doneButton.addEventListener("click", () => {
      showMessage("", null);
      setEditingState(false);
    });

    addForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      showMessage("", null);
      if (!departmentId) {
        showMessage("ID отдела не найден.", "error");
        return;
      }
      const employeeId = employeeSelect ? employeeSelect.value : "";
      if (!employeeId) {
        showMessage("Выберите сотрудника.", "error");
        return;
      }
      const positionValue = positionInput ? positionInput.value.trim() : "";
      if (positionInput && !positionInput.disabled && !positionValue) {
        showMessage("Укажите должность.", "error");
        return;
      }
      const rateValue = rateInput ? rateInput.value.trim() : "";
      const rateError = validateRate(rateValue);
      if (rateError) {
        showMessage(rateError, "error");
        return;
      }

      const updateUrl = buildEmployeeUpdateUrl(employeeId);
      if (!updateUrl) {
        showMessage("URL обновления сотрудника не задан.", "error");
        return;
      }

      const payload = {
        department_id: Number(departmentId),
      };
      if (positionValue) {
        payload.position = positionValue;
      }
      if (rateValue) {
        payload.hourly_rate = Number(rateValue.replace(",", "."));
      }

      const submitButton = addForm.querySelector("button[type='submit']");
      if (submitButton) {
        submitButton.disabled = true;
      }
      try {
        const response = await fetch(updateUrl, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCookie("csrftoken"),
          },
          credentials: "same-origin",
          body: JSON.stringify(payload),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          showMessage(data.detail || "Не удалось добавить сотрудника.", "error");
          return;
        }

        if (listBody) {
          const row = renderEmployeeRow(data);
          listBody.append(row);
        }
        const currentEmptyRow = getEmptyRow();
        if (currentEmptyRow) {
          currentEmptyRow.remove();
        }
        updateEmployeeCount(1);
        if (employeeSelect) {
          const option = employeeSelect.querySelector(`option[value='${employeeId}']`);
          if (option) {
            option.remove();
          }
          employeeSelect.value = "";
          const hasOptions = employeeSelect.options.length > 1;
          employeeSelect.disabled = !hasOptions;
          refreshCustomSelect(employeeSelect.closest("[data-custom-select]"));
          if (!hasOptions && submitButton) {
            submitButton.disabled = true;
          }
        }
        if (positionInput) {
          positionInput.value = "";
          if (positionInput.tagName === "SELECT") {
            refreshCustomSelect(positionInput.closest("[data-custom-select]"));
          }
        }
        if (rateInput) {
          rateInput.value = "";
        }
        showMessage("Сотрудник добавлен в отдел.", "success");
      } catch (error) {
        showMessage("Ошибка сети. Попробуйте позже.", "error");
      } finally {
        if (submitButton) {
          submitButton.disabled = false;
        }
      }
    });

    if (listBody) {
      listBody.addEventListener("click", (event) => {
        const transferButton = event.target.closest("[data-employee-transfer]");
        if (!transferButton) {
          return;
        }
        if (!section.classList.contains("is-editing")) {
          return;
        }
        if (transferDepartmentSelect && transferDepartmentSelect.disabled) {
          showMessage("Нет доступных отделов для перевода.", "error");
          return;
        }
        const row = transferButton.closest("tr");
        if (!row) {
          return;
        }
        openTransferModal(row);
      });
    }

    if (transferForm) {
      transferForm.addEventListener("submit", async (event) => {
        event.preventDefault();
        showTransferMessage("", null);
        if (!transferTarget || !transferTarget.id) {
          showTransferMessage("Сотрудник не выбран.", "error");
          return;
        }
        const departmentValue = transferDepartmentSelect ? transferDepartmentSelect.value : "";
        if (!departmentValue) {
          showTransferMessage("Выберите отдел.", "error");
          return;
        }
        const reasonValue = transferReasonInput ? transferReasonInput.value.trim() : "";
        if (!reasonValue) {
          showTransferMessage("Укажите причину перевода.", "error");
          return;
        }
        const updateUrl = buildEmployeeUpdateUrl(transferTarget.id);
        if (!updateUrl) {
          showTransferMessage("URL обновления сотрудника не задан.", "error");
          return;
        }
        const submitButton = transferForm.querySelector("button[type='submit']");
        if (submitButton) {
          submitButton.disabled = true;
        }
        try {
          const response = await fetch(updateUrl, {
            method: "PATCH",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": getCookie("csrftoken"),
            },
            credentials: "same-origin",
            body: JSON.stringify({
              department_id: Number(departmentValue),
              department_change_reason: reasonValue,
            }),
          });
          const data = await response.json().catch(() => ({}));
          if (!response.ok) {
            showTransferMessage(data.detail || "Не удалось перевести сотрудника.", "error");
            return;
          }
          if (transferTarget.row) {
            transferTarget.row.remove();
          }
          updateEmployeeCount(-1);
          if (listBody && !listBody.querySelector("tr:not([data-empty-row])")) {
            ensureEmptyRow();
          }
          addEmployeeOption({
            id: transferTarget.id,
            full_name: transferTarget.name,
            email: transferTarget.email,
          });
          transferTarget = null;
          showMessage("Сотрудник переведен в другой отдел.", "success");
          closeModal(transferModal);
        } catch (error) {
          showTransferMessage("Ошибка сети. Попробуйте позже.", "error");
        } finally {
          if (submitButton) {
            submitButton.disabled = false;
          }
        }
      });
    }

    setEditingState(false);
    initInfoEditing();
  };

  initDepartmentDetail();
})();
