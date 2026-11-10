(() => {
    'use strict';
    const config = JSON.parse(document.getElementById('planner-config').textContent);
    const el = id => document.getElementById(id);
    const state = {
        data: null, kitchen: null, selected: new Set(), tab: 'orders', viewedSession: null,
        quoteEdited: false, saving: false, loading: false, generation: 0, controller: null,
        clockOffset: 0, drawing: false, drag: null, expiryRefresh: false, map: null, layers: null,
    };
    const textNode = (tag, text, className) => {
        const node = document.createElement(tag);
        node.textContent = text;
        if (className) node.className = className;
        return node;
    };
    const button = (text, className, action) => {
        const node = textNode('button', text, className);
        node.type = 'button';
        node.addEventListener('click', action);
        return node;
    };
    const money = value => Number(value || 0).toLocaleString('en-LK', {
        minimumFractionDigits: 2, maximumFractionDigits: 2,
    });
    const windowText = seconds => seconds % 60 === 0 ? `${seconds / 60} min` : `${seconds} sec`;
    const timeText = value => new Date(value).toLocaleTimeString('en-LK', {
        timeZone: 'Asia/Colombo', hour: '2-digit', minute: '2-digit', hour12: true,
    });
    const allOrders = () => state.data?.orders || [];
    const sessions = () => state.data?.sessions || [];
    const riders = () => state.data?.riders || [];
    const kitchen = () => state.data?.vendors.find(item => item.id === state.kitchen);
    const selectedOrders = () => allOrders().filter(order => state.selected.has(order.id));

    function visibleOrders() {
        const query = el('order-search').value.trim().toLocaleLowerCase();
        return allOrders().filter(order => {
            if (state.kitchen && order.vendor.id !== state.kitchen) return false;
            if (el('available-only').checked && !order.selectable) return false;
            return !query || [order.id, order.customer_name, order.vendor.name, order.location?.name,
                order.location?.description, ...order.items.map(item => item.name)]
                .join(' ').toLocaleLowerCase().includes(query);
        });
    }

    function showMessage(message, error = false, focus = false) {
        const node = el('planner-message');
        node.hidden = false;
        node.classList.toggle('error', error);
        node.setAttribute('role', error ? 'alert' : 'status');
        node.textContent = message;
        if (focus) node.focus({preventScroll: true});
    }

    function csrfToken() {
        const input = document.querySelector('#session-form [name=csrfmiddlewaretoken]');
        if (input) return input.value;
        const cookie = document.cookie.split(';').map(value => value.trim())
            .find(value => value.startsWith('csrftoken='));
        return cookie ? decodeURIComponent(cookie.slice('csrftoken='.length)) : '';
    }

    async function request(url, options = {}) {
        const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', ...options});
        if (response.status === 403 && !options.method) {
            window.location.assign(`${config.loginUrl}?next=${encodeURIComponent(window.location.pathname)}`);
            throw new Error('Sign in with an admin account.');
        }
        let data;
        try { data = await response.json(); }
        catch { throw new Error('The server did not return the expected response. Refresh and try again.'); }
        if (!response.ok) {
            const error = new Error(data.error || 'The request failed. Refresh and try again.');
            error.fields = data.fields;
            throw error;
        }
        return data;
    }

    function riderOptions(select, includeAutomatic = false, includePlanned = false) {
        const previous = select.value;
        select.replaceChildren(new Option(includeAutomatic ? 'Choose rider or automatic' : 'Choose an eligible rider', ''));
        if (includeAutomatic) select.add(new Option('Automatic rider offers', 'automatic'));
        if (includePlanned) select.add(new Option('Save as planned', 'planned'));
        riders().forEach(rider => {
            const detail = rider.eligible ? windowText(rider.acceptance_window) : rider.reason;
            const option = new Option(`${rider.phone} · ${rider.name} · ${detail}`, String(rider.id));
            option.disabled = !rider.eligible;
            select.add(option);
        });
        if ([...select.options].some(option => option.value === previous)) select.value = previous;
    }

    async function loadData({fit = false} = {}) {
        if (state.saving) return;
        const generation = ++state.generation;
        state.controller?.abort();
        state.controller = new AbortController();
        state.loading = true;
        el('refresh-data').disabled = true;
        el('refresh-data').textContent = 'Refreshing…';
        try {
            const data = await request(`${config.dataUrl}?date=${encodeURIComponent(el('planning-date').value)}`,
                {signal: state.controller.signal});
            if (generation !== state.generation || state.saving) return;
            const firstLoad = state.data === null;
            state.data = data;
            state.clockOffset = Date.parse(data.server_time) - Date.now();
            state.expiryRefresh = false;
            const previousKitchen = state.kitchen;
            const selector = el('kitchen-filter');
            selector.replaceChildren(new Option('All kitchens · choose one to plan', ''));
            data.vendors.forEach(vendor => selector.add(new Option(vendor.name, String(vendor.id))));
            if (!data.vendors.some(vendor => vendor.id === previousKitchen)) state.kitchen = null;
            if (firstLoad && data.vendors.length === 1) state.kitchen = data.vendors[0].id;
            selector.value = state.kitchen === null ? '' : String(state.kitchen);
            state.selected = new Set(data.orders.filter(order => order.selectable
                && order.vendor.id === state.kitchen && state.selected.has(order.id)).map(order => order.id));
            if (!data.sessions.some(session => session.id === state.viewedSession)) state.viewedSession = null;
            el('history-note').hidden = data.date === data.today;
            el('order-count').textContent = data.orders.length;
            el('session-count').textContent = data.sessions.length;
            el('order-list').setAttribute('aria-busy', 'false');
            riderOptions(el('new-rider'), true, true);
            renderAll();
            if (fit || firstLoad) fitMap();
            updateClock();
        } catch (error) {
            if (error.name === 'AbortError') return;
            showMessage(error.message, true);
            if (!state.data) {
                const empty = emptyState('Orders could not load', 'Check your connection and refresh the orders.');
                empty.append(button('Try again', 'text-button', () => loadData({fit: true})));
                el('order-list').replaceChildren(empty);
                el('order-list').setAttribute('aria-busy', 'false');
            }
        } finally {
            if (generation === state.generation) {
                state.loading = false;
                el('refresh-data').disabled = state.saving;
                el('refresh-data').textContent = 'Refresh orders';
            }
        }
    }

    function emptyState(title, description) {
        const node = textNode('div', '', 'empty-state');
        node.append(textNode('strong', title), textNode('p', description));
        return node;
    }

    function chooseKitchen() {
        stopDrawing();
        state.kitchen = el('kitchen-filter').value ? Number(el('kitchen-filter').value) : null;
        state.selected.clear();
        state.quoteEdited = false;
        state.viewedSession = null;
        setTab('orders');
        fitMap();
    }

    function setSelection(ids, checked = true, {popup = null, focus = null} = {}) {
        if (!state.kitchen) {
            showMessage('Choose one kitchen before selecting its orders.', true);
            el('kitchen-filter').focus();
            return;
        }
        const selectable = new Set(allOrders().filter(order => order.selectable
            && order.vendor.id === state.kitchen).map(order => order.id));
        const next = new Set(state.selected);
        ids.forEach(id => {
            if (checked && selectable.has(id)) next.add(id);
            else next.delete(id);
        });
        if (next.size > 100) {
            showMessage('A session can contain up to 100 orders. Select a smaller group.', true);
            return;
        }
        state.selected = next;
        state.viewedSession = null;
        el('form-error').hidden = true;
        renderAll({popup});
        if (focus) requestAnimationFrame(() => el(focus)?.focus({preventScroll: true}));
    }

    function clearSelection() {
        state.selected.clear();
        state.quoteEdited = false;
        el('service-charge').value = '0.00';
        el('fuel-surge').value = '0.00';
        el('form-error').hidden = true;
        renderAll();
    }

    function lockPlanningControls(locked) {
        document.querySelectorAll('#session-form input:not([type=hidden]), #session-form select, '
            + '#planner-filters input, #planner-filters select, #order-search, #available-only, '
            + '#session-list button, #session-list select, .planner-tabs button')
            .forEach(node => {node.disabled = locked;});
        el('refresh-data').disabled = locked || state.loading;
        el('session-form').setAttribute('aria-busy', String(locked));
        if (locked) {stopDrawing(); state.map?.closePopup();}
        renderOrders();
        updateForm();
    }

    function renderOrders() {
        const fragment = document.createDocumentFragment();
        const visible = visibleOrders();
        const available = visible.filter(order => order.selectable).length;
        el('selection-hint').textContent = state.kitchen
            ? `${available} available · ${visible.length} shown`
            : 'Choose a kitchen to select its orders.';
        el('select-shown').disabled = !state.kitchen || !available || state.saving;
        if (!visible.length) {
            const empty = emptyState(allOrders().length ? 'No matching orders' : 'No orders for this day',
                allOrders().length ? 'Clear the search or change the kitchen filter.' : 'New customer orders will appear here. You can also choose an earlier day to view its sessions.');
            if (sessions().length) empty.append(button('View sessions', 'text-button', () => setTab('sessions')));
            fragment.append(empty);
        }
        visible.forEach(order => {
            const row = textNode('div', '', 'order-row');
            row.setAttribute('role', 'listitem');
            row.classList.toggle('is-selected', state.selected.has(order.id));
            const checkbox = document.createElement('input');
            checkbox.type = 'checkbox';
            checkbox.id = `order-check-${order.id}`;
            checkbox.checked = state.selected.has(order.id);
            checkbox.disabled = !order.selectable || !state.kitchen || state.saving;
            checkbox.addEventListener('change', () => setSelection([order.id], checkbox.checked, {focus: checkbox.id}));
            const body = textNode('div', '', 'order-row-main');
            const heading = textNode('div', '', 'order-row-heading');
            const label = textNode('label', `Order #${order.id}`);
            label.htmlFor = checkbox.id;
            heading.append(label, textNode('span', `Fee LKR ${money(order.delivery_fee)}`));
            body.append(heading, textNode('p', order.location?.name || 'Missing delivery location', 'order-location'));
            body.append(textNode('p', `${order.customer_name} · ${order.items.map(item => `${item.quantity} × ${item.name}`).join(', ') || 'No meal details'}`, 'order-meta'));
            if (!state.kitchen) body.append(textNode('p', order.vendor.name, 'order-meta'));
            if (order.blocked_reason) {
                const reason = textNode('div', '', 'order-blocked');
                if (order.session_id && sessions().some(session => session.id === order.session_id)) {
                    reason.append(button(order.blocked_reason, 'text-button', () => viewSession(order.session_id)));
                } else reason.textContent = order.blocked_reason;
                body.append(reason);
            }
            row.append(checkbox, body);
            fragment.append(row);
        });
        el('order-list').replaceChildren(fragment);
    }

    function updateForm() {
        const selected = selectedOrders();
        const stopCount = new Set(selected.map(order => order.location.id)).size;
        const feeCents = selected.reduce((total, order) => total + Math.round(Number(order.delivery_fee) * 100), 0);
        el('selection-summary').textContent = selected.length
            ? `${selected.length} order${selected.length === 1 ? '' : 's'} · ${stopCount} stop${stopCount === 1 ? '' : 's'}`
            : '0 orders selected';
        el('delivery-fees').textContent = `Combined delivery fees: LKR ${money(feeCents / 100)}`;
        if (!state.quoteEdited) el('rider-cut').value = (feeCents / 100).toFixed(2);
        el('clear-selection').disabled = !selected.length || state.saving;
        const mode = dispatchMode();
        const rider = riders().find(item => item.id === Number(el('new-rider').value));
        el('offer-window').textContent = mode === 'planned'
            ? 'Save the session now and choose a rider in the Sessions tab later.'
            : mode === 'automatic' ? 'The dispatcher sends timed offers to eligible riders.'
            : rider?.eligible
            ? `Acceptance window: ${windowText(rider.acceptance_window)}. Rider earnings: LKR ${money(Number(el('rider-cut').value) + Number(el('fuel-surge').value))}.`
            : 'Choose an eligible rider. Busy riders and incomplete profiles are unavailable.';
        const create = el('create-session');
        create.textContent = state.saving ? 'Saving…'
            : mode === 'planned' ? 'Create planned session'
                : mode === 'automatic' ? 'Create session & dispatch' : 'Create session & offer';
        create.disabled = state.saving || !selected.length || !state.kitchen
            || state.data?.date !== state.data?.today || (mode === 'rider' && !rider?.eligible);
        el('draw-area').disabled = !state.kitchen || state.viewedSession !== null
            || !visibleOrders().some(order => order.selectable) || state.saving || !state.map;
    }

    function dispatchMode() {
        const value = el('new-rider').value;
        return ['automatic', 'planned'].includes(value) ? value : 'rider';
    }

    function showFormError(error) {
        const node = el('form-error');
        const names = {rider_cut: 'rider-cut', service_charge: 'service-charge',
            fuel_surge_addition: 'fuel-surge', offer_to_rider: 'new-rider', vendor: 'kitchen-filter'};
        const details = [];
        Object.entries(error.fields || {}).forEach(([name, messages]) => {
            if (names[name]) el(names[name]).setAttribute('aria-invalid', 'true');
            details.push(`${name.replaceAll('_', ' ')}: ${Array.isArray(messages) ? messages.join(' ') : String(messages)}`);
        });
        node.textContent = details.length ? details.join(' · ') : error.message;
        node.hidden = false;
    }

    async function submitSession(event) {
        event.preventDefault();
        if (state.saving || !state.selected.size || !el('session-form').reportValidity()) return;
        const mode = dispatchMode();
        const payload = {vendor: state.kitchen, orders: [...state.selected],
            date: el('planning-date').value, rider_cut: el('rider-cut').value,
            service_charge: el('service-charge').value, fuel_surge_addition: el('fuel-surge').value,
            dispatch_enabled: mode === 'automatic'};
        if (mode === 'rider') payload.offer_to_rider = Number(el('new-rider').value);
        el('form-error').hidden = true;
        document.querySelectorAll('[aria-invalid]').forEach(node => node.removeAttribute('aria-invalid'));
        state.saving = true;
        lockPlanningControls(true);
        try {
            const data = await request(config.createUrl, {
                method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken()},
                body: JSON.stringify(payload),
            });
            state.selected.clear();
            state.quoteEdited = false;
            el('service-charge').value = '0.00';
            el('fuel-surge').value = '0.00';
            state.viewedSession = data.session.id;
            state.saving = false;
            await loadData();
            setTab('sessions');
            viewSession(data.session.id);
            const offer = data.session.offer;
            showMessage(offer?.status === 'pending'
                ? `Session #${data.session.id} created. Offer #${offer.id} sent to ${offer.rider_phone}; expires at ${timeText(offer.expires_at)} Sri Lanka time.`
                : `Session #${data.session.id} created. ${data.session.dispatch_enabled ? 'Automatic dispatch is waiting for an eligible rider.' : 'Choose a rider in Sessions when you are ready to send an offer.'}`, false, true);
        } catch (error) {
            showFormError(error);
        } finally {
            state.saving = false;
            lockPlanningControls(false);
        }
    }

    function setTab(tab) {
        state.tab = tab;
        ['orders', 'sessions'].forEach(name => {
            const active = name === tab;
            el(`${name}-tab`).setAttribute('aria-selected', String(active));
            el(`${name}-tab`).tabIndex = active ? 0 : -1;
        });
        el('order-view').hidden = tab !== 'orders';
        el('sessions-view').hidden = tab !== 'sessions';
        if (tab === 'orders') state.viewedSession = null;
        renderAll();
        if (state.map) requestAnimationFrame(() => state.map.invalidateSize());
    }

    function renderSessions() {
        const fragment = document.createDocumentFragment();
        const filtered = sessions().filter(session => !state.kitchen || session.vendor?.id === state.kitchen);
        el('sessions-summary').textContent = `${filtered.length} session${filtered.length === 1 ? '' : 's'} for ${state.data?.date || 'this day'}. Open a session to see its stops or send a new offer.`;
        if (!filtered.length) {
            fragment.append(emptyState('No sessions yet', 'Select available orders in the Orders tab to create the first delivery run.'));
        }
        filtered.forEach(session => {
            const row = textNode('article', '', 'session-row');
            row.dataset.sessionId = session.id;
            row.setAttribute('role', 'listitem');
            row.classList.toggle('is-viewed', state.viewedSession === session.id);
            row.append(textNode('h3', `Session #${session.id} · ${session.vendor?.name || 'Deleted kitchen'}`));
            row.append(textNode('p', `${session.status_display} · ${session.order_count} orders · ${session.stop_count} stops`, 'session-status'));
            row.append(textNode('p', `${session.finance_status === 'confirmed' ? 'Confirmed' : 'Draft'} rider earnings: LKR ${money(session.rider_revenue)}`));
            if (session.rider_name) row.append(textNode('p', `Assigned rider: ${session.rider_name}`));
            if (session.offer) {
                const offer = session.offer;
                const line = textNode('p', `Offer #${offer.id}: ${offer.status} · ${offer.rider_phone}`, 'offer-status');
                if (offer.status === 'pending') {
                    const countdown = textNode('span', '');
                    countdown.dataset.expiresAt = offer.expires_at;
                    line.append(textNode('span', ' · '), countdown);
                }
                row.append(line);
            } else if (session.dispatch_enabled) row.append(textNode('p', 'Automatic dispatch is waiting for an eligible rider.', 'offer-status'));
            else if (session.status === 'planned') row.append(textNode('p', 'Ready for a rider offer.', 'offer-status'));
            const actions = textNode('div', '', 'session-row-actions');
            actions.append(button('View stops on map', 'text-button', () => viewSession(session.id)));
            const link = textNode('a', 'Open in admin');
            link.href = session.admin_url;
            actions.append(link);
            row.append(actions);
            if (session.can_offer) {
                const form = document.createElement('form');
                form.className = 'session-offer-form';
                const select = document.createElement('select');
                select.id = `offer-rider-${session.id}`;
                select.required = true;
                const label = textNode('label', 'Send a fresh job offer');
                label.htmlFor = select.id;
                riderOptions(select, true);
                const controls = textNode('div', '', 'session-offer-controls');
                const submit = textNode('button', 'Offer job', 'button primary small');
                submit.type = 'submit';
                submit.disabled = state.saving;
                controls.append(select, submit);
                const error = textNode('p', '', 'session-offer-error');
                error.setAttribute('role', 'alert');
                error.hidden = true;
                form.append(label, controls, error);
                form.addEventListener('submit', event => submitOffer(event, session, select, submit, error));
                row.append(form);
            }
            fragment.append(row);
        });
        el('session-list').replaceChildren(fragment);
        updateCountdowns();
    }

    async function submitOffer(event, session, select, submit, errorNode) {
        event.preventDefault();
        if (state.saving || !select.value) return;
        errorNode.hidden = true;
        state.saving = true;
        lockPlanningControls(true);
        submit.disabled = true;
        submit.textContent = 'Offering…';
        updateForm();
        const payload = select.value === 'automatic' ? {automatic: true} : {rider_id: Number(select.value)};
        try {
            const data = await request(config.offerUrl.replace('/0/offer/', `/${session.id}/offer/`), {
                method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrfToken()},
                body: JSON.stringify(payload),
            });
            state.saving = false;
            await loadData();
            const offer = data.session.offer;
            showMessage(offer?.status === 'pending'
                ? `Offer #${offer.id} sent to ${offer.rider_phone} for session #${session.id}. Expires at ${timeText(offer.expires_at)} Sri Lanka time.`
                : `Session #${session.id} is queued for automatic rider offers.`, false, true);
        } catch (error) {
            errorNode.textContent = error.message;
            errorNode.hidden = false;
        } finally {
            state.saving = false;
            submit.disabled = false;
            submit.textContent = 'Offer job';
            lockPlanningControls(false);
        }
    }

    function viewSession(id) {
        if (!sessions().some(session => session.id === id)) return;
        stopDrawing();
        state.viewedSession = id;
        setTab('sessions');
        fitMap();
        el('map-title').scrollIntoView({block: 'nearest', behavior: 'auto'});
    }

    function haversine(a, b) {
        const radians = value => value * Math.PI / 180;
        const term = Math.sin(radians(b.lat - a.lat) / 2) ** 2
            + Math.cos(radians(a.lat)) * Math.cos(radians(b.lat)) * Math.sin(radians(b.lng - a.lng) / 2) ** 2;
        return 6371.0088 * 2 * Math.atan2(Math.sqrt(term), Math.sqrt(Math.max(0, 1 - term)));
    }

    function groupedOrders(orders) {
        const grouped = new Map();
        orders.filter(order => order.map_ready).forEach(order => {
            const location = order.location;
            if (!grouped.has(location.id)) grouped.set(location.id, {...location, orders: []});
            grouped.get(location.id).orders.push(order);
        });
        return [...grouped.values()];
    }

    function popupFor(group) {
        const content = textNode('div', '');
        content.append(textNode('h3', group.name));
        if (group.description) content.append(textNode('p', group.description));
        if (!group.orders) {
            content.append(textNode('p', `Orders: ${group.order_ids.map(id => `#${id}`).join(', ')}`));
            content.append(textNode('p', `${group.remaining_orders} deliveries remaining`));
            return content;
        }
        if (!state.kitchen) content.append(textNode('p', 'Choose a kitchen above to select its orders.'));
        group.orders.forEach(order => {
            const row = textNode('div', '', 'popup-order');
            const check = document.createElement('input');
            check.type = 'checkbox';
            check.id = `popup-order-${order.id}`;
            check.checked = state.selected.has(order.id);
            check.disabled = !order.selectable || !state.kitchen || state.saving;
            check.addEventListener('change', () => setSelection([order.id], check.checked, {popup: group.id, focus: check.id}));
            const label = textNode('label', `Order #${order.id} · LKR ${money(order.total_amount)}`);
            label.htmlFor = check.id;
            label.append(textNode('small', `${order.customer_name} · ${order.blocked_reason || order.status_display}`));
            row.append(check, label);
            content.append(row);
        });
        return content;
    }

    function renderMap({popup = null} = {}) {
        const session = sessions().find(item => item.id === state.viewedSession);
        el('map-title').textContent = session ? `Session #${session.id} · ${session.stop_count} stops` : 'Delivery map';
        el('back-to-orders').hidden = !session;
        if (!state.map) return;
        state.layers.clearLayers();
        if (state.data?.delivery_area) {
            L.geoJSON(state.data.delivery_area, {style: {color: '#357b57', weight: 1, fillColor: '#35d5a2', fillOpacity: 0.035},
                interactive: false}).addTo(state.layers);
        }
        const pickup = session?.vendor || kitchen();
        let groups = session ? session.stops : groupedOrders(visibleOrders());
        const selected = session ? groups : groups.filter(group => group.orders.some(order => state.selected.has(order.id)));
        if (pickup) selected.sort((a, b) => haversine(pickup, a) - haversine(pickup, b) || a.id - b.id);
        const numbers = new Map(selected.map((group, index) => [group.id, index + 1]));
        if (pickup && selected.length) {
            L.polyline([[pickup.lat, pickup.lng], ...selected.map(group => [group.lat, group.lng])],
                {color: '#008961', weight: 3, opacity: 0.9, interactive: false}).addTo(state.layers);
        }
        const pickups = pickup ? [pickup] : state.data?.vendors || [];
        pickups.filter(vendor => Number.isFinite(vendor.lat) && Number.isFinite(vendor.lng)).forEach(vendor => {
            const icon = L.divIcon({className: 'pickup-pin', iconSize: [34, 34], iconAnchor: [17, 17],
                html: '<span class="pin-face"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 10V20H20V10M3 10L5 4H19L21 10M8 20V13H16V20M3 10H21" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg></span>'});
            const marker = L.marker([vendor.lat, vendor.lng], {icon, title: `Kitchen pickup: ${vendor.name}`}).addTo(state.layers);
            marker.bindPopup(textNode('p', `Kitchen pickup · ${vendor.name}`));
        });
        groups.forEach(group => {
            const isSelected = numbers.has(group.id);
            const available = group.orders?.some(order => order.selectable);
            const count = group.orders?.length || group.order_ids.length;
            const face = isSelected ? numbers.get(group.id) : count;
            const style = isSelected ? 'pin-selected' : available ? 'pin-available' : 'pin-reserved';
            const icon = L.divIcon({className: `delivery-pin ${style}`, iconSize: [34, 34], iconAnchor: [17, 17],
                html: `<span class="pin-face">${face}</span>`});
            const marker = L.marker([group.lat, group.lng], {icon, title: `${group.name} · ${count} orders`})
                .addTo(state.layers).bindPopup(popupFor(group));
            marker.getElement().setAttribute('aria-label', `${group.name}, ${count} orders${isSelected ? `, stop ${face}` : ''}`);
            if (popup === group.id) marker.openPopup();
        });
        el('map-caption').textContent = session
            ? 'Saved stop sequence. Riders open driving directions from their app.'
            : selected.length ? `${selectedOrders().length} selected orders at ${selected.length} stops. Lines preview the stop sequence; riders open driving directions in their app.`
                : 'Click a delivery pin to see its orders. Selected stops follow distance from the kitchen.';
    }

    function fitMap() {
        if (!state.map || !state.data) return;
        state.map.invalidateSize();
        const session = sessions().find(item => item.id === state.viewedSession);
        const pickup = session?.vendor || kitchen();
        const stops = session ? session.stops : groupedOrders(selectedOrders());
        if (stops.length && pickup) {
            state.map.fitBounds(L.latLngBounds([[pickup.lat, pickup.lng], ...stops.map(stop => [stop.lat, stop.lng])]),
                {padding: [46, 46], maxZoom: 17, animate: false});
        } else if (state.data.delivery_area) {
            state.map.fitBounds(L.geoJSON(state.data.delivery_area).getBounds(), {padding: [22, 22], animate: false});
        } else state.map.setView([5.94, 80.577], 14, {animate: false});
    }

    function stopDrawing() {
        if (!state.map) return;
        if (state.drag?.rectangle) state.map.removeLayer(state.drag.rectangle);
        state.drag = null;
        state.drawing = false;
        state.map.dragging.enable();
        state.map.touchZoom.enable();
        el('order-map').classList.remove('is-drawing');
        el('area-instructions').hidden = true;
        el('draw-area').setAttribute('aria-pressed', 'false');
    }

    function initializeMap() {
        if (typeof L === 'undefined') {
            el('map-unavailable').hidden = false;
            return;
        }
        state.map = L.map('order-map', {scrollWheelZoom: false, boxZoom: false, fadeAnimation: false}).setView([5.94, 80.577], 14);
        state.layers = L.layerGroup().addTo(state.map);
        const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
        }).addTo(state.map);
        tiles.on('tileerror', () => {
            el('map-caption').textContent = 'Map tiles are unavailable. Delivery pins and selection still work. Refresh to retry.';
        });
        el('refresh-data').addEventListener('click', () => tiles.redraw());
        const container = el('order-map');
        container.addEventListener('pointerdown', event => {
            if (!state.drawing || event.button !== 0) return;
            event.preventDefault();
            event.stopPropagation();
            container.setPointerCapture(event.pointerId);
            state.drag = {start: state.map.mouseEventToLatLng(event), pointer: event.pointerId, rectangle: null};
        }, true);
        container.addEventListener('pointermove', event => {
            if (!state.drag) return;
            event.preventDefault();
            event.stopPropagation();
            const bounds = L.latLngBounds(state.drag.start, state.map.mouseEventToLatLng(event));
            if (!state.drag.rectangle) state.drag.rectangle = L.rectangle(bounds,
                {color: '#00694b', weight: 2, fillColor: '#35d5a2', fillOpacity: 0.2, interactive: false}).addTo(state.map);
            else state.drag.rectangle.setBounds(bounds);
        }, true);
        container.addEventListener('pointerup', event => {
            if (!state.drag) return;
            event.preventDefault();
            event.stopPropagation();
            const bounds = state.drag.rectangle?.getBounds();
            container.releasePointerCapture(event.pointerId);
            stopDrawing();
            if (!bounds) return;
            const ids = visibleOrders().filter(order => order.selectable && order.map_ready
                && bounds.contains([order.location.lat, order.location.lng])).map(order => order.id);
            if (!ids.length) showMessage('No available orders in that area. Draw around another group of pins.', true);
            else setSelection(ids);
        }, true);
        container.addEventListener('pointercancel', stopDrawing, true);
    }

    function renderAll(options = {}) {
        renderOrders();
        renderSessions();
        updateForm();
        renderMap(options);
    }

    function updateCountdowns() {
        document.querySelectorAll('[data-expires-at]').forEach(node => {
            const remaining = Math.ceil((Date.parse(node.dataset.expiresAt) - Date.now() - state.clockOffset) / 1000);
            node.textContent = remaining > 0 ? `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')} left` : 'Expired';
            if (remaining <= 0 && !state.loading && !state.saving && !state.expiryRefresh) {
                state.expiryRefresh = true;
                setTimeout(() => loadData(), 0);
            }
        });
    }

    function updateClock() {
        el('live-day').textContent = new Date(Date.now() + state.clockOffset).toLocaleString('en-LK', {
            timeZone: 'Asia/Colombo', weekday: 'short', day: 'numeric', month: 'short',
            hour: '2-digit', minute: '2-digit', hour12: true,
        }) + ' · Sri Lanka';
        updateCountdowns();
    }

    el('planner-filters').addEventListener('submit', event => event.preventDefault());
    el('planning-date').addEventListener('change', () => {
        state.selected.clear();
        state.viewedSession = null;
        state.quoteEdited = false;
        stopDrawing();
        loadData({fit: true});
    });
    el('kitchen-filter').addEventListener('change', chooseKitchen);
    el('refresh-data').addEventListener('click', () => loadData());
    el('order-search').addEventListener('input', () => {renderOrders(); updateForm(); renderMap();});
    el('available-only').addEventListener('change', () => {renderOrders(); updateForm(); renderMap();});
    el('select-shown').addEventListener('click', () => setSelection(visibleOrders().filter(order => order.selectable).map(order => order.id)));
    el('clear-selection').addEventListener('click', clearSelection);
    el('new-rider').addEventListener('change', updateForm);
    ['rider-cut', 'fuel-surge'].forEach(id => el(id).addEventListener('input', () => {state.quoteEdited = true; updateForm();}));
    el('session-form').addEventListener('submit', submitSession);
    el('orders-tab').addEventListener('click', () => setTab('orders'));
    el('sessions-tab').addEventListener('click', () => setTab('sessions'));
    document.querySelector('.planner-tabs').addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const tab = event.key === 'Home' ? 'orders' : event.key === 'End' ? 'sessions'
            : state.tab === 'orders' ? 'sessions' : 'orders';
        setTab(tab);
        el(`${tab}-tab`).focus();
    });
    el('fit-map').addEventListener('click', fitMap);
    el('back-to-orders').addEventListener('click', () => {setTab('orders'); fitMap();});
    el('draw-area').addEventListener('click', () => {
        if (state.drawing) {stopDrawing(); return;}
        state.drawing = true;
        state.map.dragging.disable();
        state.map.touchZoom.disable();
        el('order-map').classList.add('is-drawing');
        el('area-instructions').hidden = false;
        el('draw-area').setAttribute('aria-pressed', 'true');
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && state.drawing) {stopDrawing(); el('draw-area').focus();}
    });
    initializeMap();
    loadData({fit: true});
    setInterval(updateClock, 1000);
    setInterval(() => {
        if (!document.hidden && !state.saving && !state.loading && !document.activeElement?.closest('form')) loadData();
    }, 30000);
})();
