const cashUpForm = document.querySelector(".entry-form");
const draftScope = cashUpForm?.dataset.draftKey || document.querySelector("[data-cash-up-draft-key]")?.dataset.cashUpDraftKey;
const currentUrl = new URL(window.location.href);
let tabDrafts = {};

if (draftScope) {
	try {
		tabDrafts = JSON.parse(localStorage.getItem(`fuelledger-cashup:${draftScope}`) || "{}");
	} catch {
		tabDrafts = {};
	}
	const savedSection = currentUrl.searchParams.get("saved");
	const savedMarker = currentUrl.searchParams.get("cashup_saved");
	if (savedMarker === "all") tabDrafts = {};
	else if (savedMarker) delete tabDrafts[savedMarker];
	if (savedSection) delete tabDrafts[savedSection];
	try {
		localStorage.setItem(`fuelledger-cashup:${draftScope}`, JSON.stringify(tabDrafts));
	} catch {
		tabDrafts = {};
	}
	if (savedSection || savedMarker) {
		currentUrl.searchParams.delete("saved");
		currentUrl.searchParams.delete("cashup_saved");
		window.history.replaceState({}, "", currentUrl);
	}
}

if (cashUpForm) {
	const sectionKey = cashUpForm.dataset.draftSection;
	const sectionDraft = tabDrafts[sectionKey] && typeof tabDrafts[sectionKey] === "object" ? tabDrafts[sectionKey] : {};
	const draftInput = cashUpForm.elements.namedItem("all_tab_values");
	const getControl = (name) => cashUpForm.elements.namedItem(name);
	const updateCombinedSalesTotal = () => {
		const combined = cashUpForm.querySelector("[data-sales-combined-total]");
		if (!combined) return;
		const sales = Number.parseFloat(cashUpForm.querySelector('[data-cash-up-total="sales_account"]')?.textContent) || 0;
		const arrears = Number.parseFloat(cashUpForm.querySelector('[data-cash-up-total="arrears_account"]')?.textContent) || 0;
		combined.textContent = (sales + arrears).toFixed(2);
	};
	const updateSectionTotal = (group) => {
		const total = cashUpForm.querySelector(`[data-cash-up-total="${group}"]`);
		if (!total) return;
		const inputs = cashUpForm.querySelectorAll(`[data-cash-up-total-input="${group}"]`);
		total.textContent = Array.from(inputs).reduce((sum, input) => sum + (Number.parseFloat(input.value) || 0), 0).toFixed(2);
		if (group === "sales_account" || group === "arrears_account") updateCombinedSalesTotal();
	};
	const updateAirtimeDifference = () => {
		cashUpForm.querySelectorAll('[data-repeat-container="airtime"] [data-repeat-row]').forEach((row) => {
			const difference = row.querySelector("[data-airtime-difference]");
			const slip = row.querySelector('input[name^="airtime_slip"]');
			const system = row.querySelector('input[name^="airtime_system"]');
			if (difference && slip && system) {
				difference.textContent = ((Number.parseFloat(slip.value) || 0) - (Number.parseFloat(system.value) || 0)).toFixed(2);
			}
		});
	};
	const updateAirtimeBalance = () => {
		const canEditFirstCard = cashUpForm.dataset.airtimeFirstCardEditable === "true";
		const previousBalance = Number.parseFloat(cashUpForm.dataset.airtimePreviousBalance || "NaN");
		const countInput = cashUpForm.querySelector('[name="airtime_count"]');
		const count = Number.parseInt(countInput?.value || "1", 10) || 1;
		const firstBalance = getControl("airtime_balance_1") || getControl("airtime_balance");
		let runningBalance = canEditFirstCard ? Number.parseFloat(firstBalance?.value || "NaN") : previousBalance;
		if (!Number.isFinite(runningBalance)) runningBalance = Number.isFinite(previousBalance) ? previousBalance : Number.parseFloat(firstBalance?.value || "0") || 0;
		for (let index = canEditFirstCard ? 2 : 1; index <= count; index += 1) {
			const slip = getControl(`airtime_slip_${index}`);
			const loaded = getControl(`airtime_loaded_${index}`);
			const balanceElement = getControl(`airtime_balance_${index}`);
			if (!slip || !loaded || !balanceElement) continue;
			const slipValue = Number.parseFloat(slip.value) || 0;
			const loadedValue = Number.parseFloat(loaded.value) || 0;
			runningBalance = runningBalance - slipValue + loadedValue;
			balanceElement.value = runningBalance.toFixed(2);
			if (balanceElement.readOnly || balanceElement.hasAttribute("readonly")) {
				balanceElement.setAttribute("readonly", "readonly");
				balanceElement.style.background = "#dff7e1";
			}
		}
	};
	const updateAllTotals = () => {
		cashUpForm.querySelectorAll("[data-cash-up-total]").forEach((total) => updateSectionTotal(total.dataset.cashUpTotal));
		updateAirtimeDifference();
		updateAirtimeBalance();
	};
	const saveDraft = () => {
		if (!draftScope) return;
		const values = {};
		Array.from(cashUpForm.elements).forEach((element) => {
			if (!element.name || ["all_tab_values", "csrfmiddlewaretoken", "save_mode"].includes(element.name) || ["submit", "button"].includes(element.type)) return;
			values[element.name] = element.value;
		});
		tabDrafts[sectionKey] = values;
		try {
			localStorage.setItem(`fuelledger-cashup:${draftScope}`, JSON.stringify(tabDrafts));
		} catch {
			return;
		}
	};
	const addRepeatRow = (group, button) => {
		const container = cashUpForm.querySelector(`[data-repeat-container="${group}"]`);
		const countInput = cashUpForm.querySelector(`[data-repeat-count="${group}"]`);
		if (!container || !countInput || container.children.length >= 50) return;
		const nextIndex = Number.parseInt(container.dataset.repeatNext, 10) || (container.children.length + 1);
		const row = container.lastElementChild.cloneNode(true);
		row.querySelectorAll(".errorlist").forEach((errors) => errors.remove());
		row.querySelectorAll("input").forEach((input) => {
			const baseName = input.name.replace(/_\d+$/, "");
			const fieldName = `${baseName}_${nextIndex}`;
			input.name = fieldName;
			input.id = `id_${fieldName}`;
			input.value = "";
			if (input.name.includes("airtime_balance")) {
				input.setAttribute("readonly", "readonly");
				input.style.background = "#dff7e1";
			}
		});
		row.querySelectorAll("label[for]").forEach((label) => {
			const baseFor = label.htmlFor.replace(/_\d+$/, "");
			label.htmlFor = `${baseFor}_${nextIndex}`;
			if (group === "airtime") {
				const baseName = baseFor.replace(/^id_/, "").replace(/_\d+$/, "");
				const labels = {airtime_shift_nr: "Airtime Shift Number", airtime_slip: "Airtime Slip", airtime_system: "Airtime System", airtime_loaded: "Airtime Loaded", airtime_balance: "Airtime Balance"};
				label.textContent = `${labels[baseName] || "Airtime"} ${nextIndex}:`;
			}
			else label.textContent = `${container.dataset.repeatLabel} ${nextIndex}`;
		});
		row.querySelectorAll("[data-remove-repeat]").forEach((removeButton) => {
			removeButton.disabled = false;
			removeButton.setAttribute("aria-label", `Remove Airtime entry ${nextIndex}`);
		});
		container.appendChild(row);
		container.dataset.repeatNext = nextIndex + 1;
		countInput.value = nextIndex;
		if (container.children.length >= 50 && button) button.disabled = true;
	};
	const removeRepeatRow = (button) => {
		const row = button.closest("[data-repeat-row]");
		const container = row?.parentElement;
		const countInput = cashUpForm.querySelector('[data-repeat-count="airtime"]');
		if (!row || !container || container.children.length <= 1 || row === container.firstElementChild) return;
		row.remove();
		Array.from(container.children).forEach((entry, offset) => {
			const index = offset + 1;
			entry.querySelectorAll("input").forEach((input) => {
				const match = input.name.match(/^(airtime_(?:shift_nr|slip|system|loaded|balance))(?:_\d+)?$/);
				if (!match) return;
				input.name = match[1] === "airtime_shift_nr" && index === 1 ? match[1] : `${match[1]}_${index}`;
				input.id = `id_${input.name}`;
			});
			entry.querySelectorAll("label[for]").forEach((label) => {
				const baseName = label.htmlFor.replace(/^id_/, "").replace(/_\d+$/, "");
				const labels = {airtime_shift_nr: "Airtime Shift Number", airtime_slip: "Airtime Slip", airtime_system: "Airtime System", airtime_loaded: "Airtime Loaded", airtime_balance: "Airtime Balance"};
				label.htmlFor = `id_${baseName === "airtime_shift_nr" && index === 1 ? baseName : `${baseName}_${index}`}`;
				label.textContent = `${labels[baseName] || "Airtime"}${index > 1 ? ` ${index}` : ""}:`;
			});
			const removeButton = entry.querySelector("[data-remove-repeat]");
			if (removeButton) {
				removeButton.disabled = index === 1;
				removeButton.setAttribute("aria-label", `Remove Airtime entry ${index}`);
			}
		});
		countInput.value = container.children.length;
		container.dataset.repeatNext = container.children.length + 1;
		const addButton = cashUpForm.querySelector('[data-add-repeat="airtime"]');
		if (addButton) addButton.disabled = container.children.length >= 50;
		updateAllTotals();
		saveDraft();
	};

	cashUpForm.querySelectorAll("[data-add-repeat]").forEach((button) => button.addEventListener("click", () => {
		addRepeatRow(button.dataset.addRepeat, button);
		updateAllTotals();
		saveDraft();
	}));
	cashUpForm.addEventListener("click", (event) => {
		const removeButton = event.target.closest("[data-remove-repeat]");
		if (removeButton) removeRepeatRow(removeButton);
	});
	if (cashUpForm.dataset.hasErrors !== "true") {
		cashUpForm.querySelectorAll("[data-repeat-container]").forEach((container) => {
			const group = container.dataset.repeatContainer;
			const countInput = cashUpForm.querySelector(`[data-repeat-count="${group}"]`);
			const savedCount = Number.parseInt(sectionDraft[countInput?.name], 10) || 1;
			while (container.children.length < savedCount && container.children.length < 50) {
				addRepeatRow(group, cashUpForm.querySelector(`[data-add-repeat="${group}"]`));
			}
		});
		Object.entries(sectionDraft).forEach(([name, value]) => {
			const control = getControl(name);
			if (control && "value" in control) control.value = value;
		});
	}
	updateAllTotals();
	cashUpForm.addEventListener("input", (event) => {
		const group = event.target.dataset.cashUpTotalInput;
		if (group) updateSectionTotal(group);
		updateAirtimeDifference();
		updateAirtimeBalance();
		saveDraft();
	});
	cashUpForm.addEventListener("change", saveDraft);
	cashUpForm.addEventListener("submit", (event) => {
		const mode = event.submitter?.value || "tab";
		saveDraft();
		if (mode === "exit" && draftInput) draftInput.value = JSON.stringify(tabDrafts);
	});
}