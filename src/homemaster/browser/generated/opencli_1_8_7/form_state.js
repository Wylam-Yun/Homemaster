(() => {
      const result = { forms: [], orphanFields: [] };

      // Collect all forms
      for (const form of document.forms) {
        const formData = {
          id: form.id || null,
          name: form.name || null,
          action: form.action || null,
          method: (form.method || 'get').toUpperCase(),
          fields: [],
        };
        for (const el of form.elements) {
          const field = extractField(el);
          if (field) formData.fields.push(field);
        }
        if (formData.fields.length > 0) result.forms.push(formData);
      }

      // Collect orphan fields (not inside a form)
      const allInputs = document.querySelectorAll('input, textarea, select, [contenteditable="true"]');
      for (const el of allInputs) {
        if (el.form) continue; // already in a form
        const field = extractField(el);
        if (field) result.orphanFields.push(field);
      }

      function extractField(el) {
        const tag = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || (tag === 'textarea' ? 'textarea' : tag === 'select' ? 'select' : 'text')).toLowerCase();
        if (type === 'hidden' || type === 'submit' || type === 'button' || type === 'reset') return null;
        const name = el.name || el.id || null;
        const ref = el.getAttribute('data-opencli-ref') || null;
        const label = findLabel(el);
        let value;
        if (tag === 'select') {
          const opt = el.options?.[el.selectedIndex];
          value = opt ? opt.textContent.trim() : '';
        } else if (type === 'checkbox' || type === 'radio') {
          value = el.checked;
        } else if (type === 'password') {
          value = el.value ? '••••' : '';
        } else if (el.isContentEditable) {
          value = (el.textContent || '').trim().slice(0, 200);
        } else {
          value = (el.value || '').slice(0, 200);
        }
        return { tag, type, name, ref, label, value, required: el.required || false, disabled: el.disabled || false };
      }

      function findLabel(el) {
        // 1. aria-label
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
        // 2. associated <label>
        if (el.id) {
          const label = document.querySelector('label[for="' + el.id + '"]');
          if (label) return label.textContent.trim().slice(0, 80);
        }
        // 3. parent label
        const parentLabel = el.closest('label');
        if (parentLabel) return parentLabel.textContent.trim().slice(0, 80);
        // 4. placeholder
        return el.placeholder || null;
      }

      return result;
    })()
