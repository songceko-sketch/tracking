(() => {
  const inputs = document.querySelectorAll('[data-location-autocomplete]');
  let instanceNumber = 0;

  function uniqueParts(parts) {
    const seen = new Set();
    return parts.filter(part => {
      const clean = (part || '').trim();
      const key = clean.toLocaleLowerCase();
      if (!clean || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function formatPlace(properties) {
    const street = [properties.housenumber, properties.street].filter(Boolean).join(' ');
    const placeName = street || properties.name;
    const locality = properties.city || properties.town || properties.village || properties.municipality;
    const parts = uniqueParts([
      placeName,
      properties.suburb,
      locality,
      properties.state,
      properties.postcode,
      properties.country,
    ]);
    return {
      address: parts.join(', '),
      detail: uniqueParts([properties.type, properties.country]).join(' · '),
    };
  }

  function initialize(input) {
    const wrapper = document.createElement('div');
    wrapper.className = 'location-autocomplete';
    input.parentNode.insertBefore(wrapper, input);
    wrapper.appendChild(input);

    const listId = `location-suggestions-${++instanceNumber}`;
    const list = document.createElement('div');
    list.id = listId;
    list.className = 'location-suggestions';
    list.setAttribute('role', 'listbox');
    list.hidden = true;
    wrapper.appendChild(list);

    input.setAttribute('autocomplete', 'off');
    input.setAttribute('role', 'combobox');
    input.setAttribute('aria-autocomplete', 'list');
    input.setAttribute('aria-expanded', 'false');
    input.setAttribute('aria-controls', listId);

    let debounceTimer;
    let abortController;
    let results = [];
    let activeIndex = -1;

    function closeList() {
      list.hidden = true;
      input.setAttribute('aria-expanded', 'false');
      input.removeAttribute('aria-activedescendant');
      activeIndex = -1;
    }

    function selectPlace(place) {
      input.value = place.address;
      input.dataset.latitude = String(place.latitude);
      input.dataset.longitude = String(place.longitude);
      input.dataset.placeId = place.placeId;
      input.dispatchEvent(new Event('change', { bubbles: true }));
      closeList();
    }

    function activate(index) {
      if (!results.length) return;
      activeIndex = (index + results.length) % results.length;
      [...list.querySelectorAll('[role="option"]')].forEach((option, optionIndex) => {
        option.setAttribute('aria-selected', String(optionIndex === activeIndex));
      });
      const activeOption = list.children[activeIndex];
      input.setAttribute('aria-activedescendant', activeOption.id);
      activeOption.scrollIntoView({ block: 'nearest' });
    }

    function showMessage(message) {
      list.replaceChildren();
      const item = document.createElement('div');
      item.className = 'location-suggestion-message';
      item.textContent = message;
      list.appendChild(item);
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    }

    function showResults(features) {
      results = features.map(feature => {
        const properties = feature.properties || {};
        const formatted = formatPlace(properties);
        return {
          ...formatted,
          latitude: feature.geometry.coordinates[1],
          longitude: feature.geometry.coordinates[0],
          placeId: `${properties.osm_type || ''}-${properties.osm_id || ''}`,
        };
      }).filter(place => place.address && Number.isFinite(place.latitude) && Number.isFinite(place.longitude));

      list.replaceChildren();
      activeIndex = -1;
      if (!results.length) {
        showMessage('No matching places found. Keep the address or try adding a city or country.');
        return;
      }

      results.forEach((place, index) => {
        const option = document.createElement('button');
        option.type = 'button';
        option.id = `${listId}-option-${index}`;
        option.className = 'location-suggestion';
        option.setAttribute('role', 'option');
        option.setAttribute('aria-selected', 'false');

        const name = document.createElement('span');
        name.className = 'location-suggestion-name';
        name.textContent = place.address;
        option.appendChild(name);
        if (place.detail) {
          const detail = document.createElement('span');
          detail.className = 'location-suggestion-detail';
          detail.textContent = place.detail;
          option.appendChild(detail);
        }

        option.addEventListener('mousedown', event => event.preventDefault());
        option.addEventListener('click', () => selectPlace(place));
        list.appendChild(option);
      });

      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
    }

    async function search(query) {
      if (abortController) abortController.abort();
      abortController = new AbortController();
      showMessage('Searching places…');

      try {
        const response = await fetch(
          `https://photon.komoot.io/api/?q=${encodeURIComponent(query)}&limit=6&lang=en`,
          { signal: abortController.signal }
        );
        if (!response.ok) throw new Error(`Place search returned ${response.status}`);
        const data = await response.json();
        showResults(data.features || []);
      } catch (error) {
        if (error.name === 'AbortError') return;
        showMessage('Place suggestions are unavailable. You can still enter the address manually.');
      }
    }

    input.addEventListener('input', () => {
      clearTimeout(debounceTimer);
      delete input.dataset.latitude;
      delete input.dataset.longitude;
      delete input.dataset.placeId;
      const query = input.value.trim();
      if (query.length < 3) {
        closeList();
        return;
      }
      debounceTimer = setTimeout(() => search(query), 300);
    });

    input.addEventListener('keydown', event => {
      if (list.hidden) return;
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        activate(activeIndex + 1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        activate(activeIndex < 0 ? results.length - 1 : activeIndex - 1);
      } else if (event.key === 'Enter' && activeIndex >= 0) {
        event.preventDefault();
        selectPlace(results[activeIndex]);
      } else if (event.key === 'Escape') {
        closeList();
      }
    });

    input.addEventListener('blur', () => setTimeout(closeList, 120));
  }

  inputs.forEach(initialize);
})();
