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
	const getFirstAirtimeControl = (fieldName) => getControl(fieldName) || getControl(`${fieldName}_1`);
	const updateAirtimeDifference = () => {
		const difference = cashUpForm.querySelector("[data-airtime-difference]");
		const slip = getFirstAirtimeControl("airtime_slip");
		const system = getFirstAirtimeControl("airtime_system");
		if (difference && slip && system) {
			difference.textContent = ((Number.parseFloat(slip.value) || 0) - (Number.parseFloat(system.value) || 0)).toFixed(2);
		}
	};
	const updateAirtimeBalance = () => {
		const previousBalance = Number.parseFloat(cashUpForm.dataset.airtimePreviousBalance || "NaN");
		const countInput = cashUpForm.querySelector('[name="airtime_count"]');
		const count = Number.parseInt(countInput?.value || "1", 10) || 1;
		let runningBalance = Number.isFinite(previousBalance) ? previousBalance : 0;
		for (let index = 1; index <= count; index += 1) {
			const slip = getControl(index === 1 ? "airtime_slip" : `airtime_slip_${index}`) || getControl(`airtime_slip_${index}`);
			const loaded = getControl(index === 1 ? "airtime_loaded" : `airtime_loaded_${index}`) || getControl(`airtime_loaded_${index}`);
			const balanceElement = getControl(index === 1 ? "airtime_balance" : `airtime_balance_${index}`) || getControl(`airtime_balance_${index}`);
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
			if (group === "airtime") label.textContent = label.textContent.replace(/\d+$/, "") + nextIndex;
			else label.textContent = `${container.dataset.repeatLabel} ${nextIndex}`;
		});
		container.appendChild(row);
		container.dataset.repeatNext = nextIndex + 1;
		countInput.value = nextIndex;
		if (container.children.length >= 50 && button) button.disabled = true;
	};

	cashUpForm.querySelectorAll("[data-add-repeat]").forEach((button) => button.addEventListener("click", () => {
		addRepeatRow(button.dataset.addRepeat, button);
		saveDraft();
	}));
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