(function () {
	"use strict";

	const currentUrl = new URL(window.location.href);
	const getStorage = (key) => {
		try {
			return localStorage.getItem(key);
		} catch {
			return null;
		}
	};
	const setStorage = (key, value) => {
		try {
			localStorage.setItem(key, value);
		} catch {
			return;
		}
	};
	const removeStorage = (key) => {
		try {
			localStorage.removeItem(key);
		} catch {
			return;
		}
	};
	const removeSessionStorage = (key) => {
		try {
			sessionStorage.removeItem(key);
		} catch {
			return;
		}
	};
	const savedForm = currentUrl.searchParams.get("workflow_saved");
	const dashboardScope = document.querySelector("[data-cash-up-draft-key]")?.dataset.cashUpDraftKey;
	if (savedForm && dashboardScope) {
		removeStorage(`fuelledger-workflow-draft:${dashboardScope}:${savedForm}`);
	}

	const makeButton = (label, className, action) => {
		const button = document.createElement("button");
		button.type = "button";
		button.className = className;
		button.textContent = label;
		button.addEventListener("click", action);
		return button;
	};
	const dialog = document.createElement("dialog");
	dialog.className = "unsaved-form-dialog";
	dialog.setAttribute("aria-modal", "true");
	const dialogTitle = document.createElement("h2");
	dialogTitle.id = "unsaved-form-title";
	dialogTitle.textContent = "Unsaved data";
	dialog.setAttribute("aria-labelledby", dialogTitle.id);
	const dialogMessage = document.createElement("p");
	dialogMessage.textContent = "This form has changes that have not been saved. What would you like to do?";
	const dialogActions = document.createElement("div");
	dialogActions.className = "unsaved-form-dialog-actions";
	const saveButton = makeButton("Save and Return", "button primary", () => {
		const pending = dialog.pendingReturn;
		if (!pending) return;
		if (pending.nextSection) {
			const nextSection = pending.form.elements.namedItem("next_section");
			const continueButton = pending.form.querySelector('button[name="save_mode"][value="continue"]')
				|| document.querySelector(`button[form="${pending.form.id}"][name="save_mode"][value="continue"]`);
			if (nextSection && continueButton) {
				nextSection.value = pending.nextSection;
				pending.form.requestSubmit(continueButton);
				return;
			}
		}
		if (pending.nextForm) {
			const nextForm = pending.form.elements.namedItem("next_form");
			const continueButton = pending.form.querySelector('button[name="save_mode"][value="continue"]')
				|| document.querySelector(`button[form="${pending.form.id}"][name="save_mode"][value="continue"]`);
			if (nextForm && continueButton) {
				nextForm.value = pending.nextForm;
				pending.form.requestSubmit(continueButton);
				return;
			}
		}
		const exitButton = pending.form.querySelector('button[name="save_mode"][value="exit"]')
			|| document.querySelector(`button[form="${pending.form.id}"][name="save_mode"][value="exit"]`);
		if (exitButton) pending.form.requestSubmit(exitButton);
	});
	const discardButton = makeButton("Discard and Return", "button secondary", () => {
		const pending = dialog.pendingReturn;
		if (!pending) return;
		if (pending.cashUp) {
			const storageKey = `fuelledger-cashup:${pending.form.dataset.draftKey}`;
			if (!pending.nextSection) {
				removeSessionStorage(storageKey);
			} else {
				try {
					const drafts = JSON.parse(sessionStorage.getItem(storageKey) || "{}");
					delete drafts[pending.form.dataset.draftSection];
					sessionStorage.setItem(storageKey, JSON.stringify(drafts));
				} catch {
					removeSessionStorage(storageKey);
				}
			}
		} else {
			removeStorage(pending.storageKey);
		}
		window.location.assign(pending.href);
	});
	const stayButton = makeButton("Keep Editing", "button secondary", () => dialog.close());
	dialogActions.append(saveButton, discardButton, stayButton);
	dialog.append(dialogTitle, dialogMessage, dialogActions);
	document.body.appendChild(dialog);

	document.querySelectorAll("form[data-workflow-draft-key]").forEach((form) => {
		const formKey = form.dataset.workflowDraftKey;
		const scope = form.dataset.workflowDraftScope;
		const cashUp = form.classList.contains("cash-up-form");
		const storageKey = `fuelledger-workflow-draft:${scope}:${formKey}`;
		if (savedForm === formKey) removeStorage(storageKey);

		let draft = null;
		if (!cashUp) {
			try {
				draft = JSON.parse(getStorage(storageKey) || "null");
			} catch {
				draft = null;
			}
		}

		const controls = () => Array.from(form.elements).filter((element) => (
			element.name
			&& !["csrfmiddlewaretoken", "save_mode", "all_tab_values"].includes(element.name)
			&& !["submit", "button"].includes(element.type)
			&& !element.disabled
		));
		const restoreDraft = () => {
			if (!draft || form.dataset.hasErrors === "true") return;
			const deliveryCount = Number.parseInt(draft.delivery_count, 10);
			const currentCount = form.elements.namedItem("delivery_count");
			const addDelivery = document.getElementById("add-delivery");
			if (currentCount && addDelivery && Number.isFinite(deliveryCount)) {
				while (Number.parseInt(currentCount.value, 10) < deliveryCount) addDelivery.click();
			}
			controls().forEach((control) => {
				if (Object.hasOwn(draft, control.name)) control.value = draft[control.name];
			});
		};
		const saveDraft = () => {
			if (cashUp) {
				form.dataset.workflowDraftDirty = "true";
				return;
			}
			const values = {};
			controls().forEach((control) => { values[control.name] = control.value; });
			setStorage(storageKey, JSON.stringify(values));
			form.dataset.workflowDraftDirty = "true";
		};

		restoreDraft();
		if (draft || form.dataset.hasErrors === "true" && !cashUp) form.dataset.workflowDraftDirty = "true";
		if (!cashUp) {
			form.addEventListener("input", saveDraft);
			form.addEventListener("change", saveDraft);
			form.addEventListener("click", (event) => {
				if (event.target.closest("#add-delivery")) window.setTimeout(saveDraft, 0);
			});
		}
	});

	document.querySelectorAll("[data-unsaved-return]").forEach((link) => {
		link.addEventListener("click", (event) => {
			const form = document.querySelector("form[data-workflow-draft-key]");
			if (!form || form.dataset.workflowDraftDirty !== "true") return;
			event.preventDefault();
			dialog.pendingReturn = {
				form,
				cashUp: form.classList.contains("cash-up-form"),
				nextSection: link.dataset.cashUpSection || "",
				nextForm: link.dataset.unsavedNextForm || "",
				storageKey: `fuelledger-workflow-draft:${form.dataset.workflowDraftScope}:${form.dataset.workflowDraftKey}`,
				href: link.href,
			};
			saveButton.textContent = dialog.pendingReturn.nextSection || dialog.pendingReturn.nextForm ? "Save and Continue" : "Save and Return";
			dialog.showModal();
		});
	});
})();