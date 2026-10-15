/* =========================================================================
   Product list page — Angular-style SPA behaviour on plain Django.
   - Reads filters from URL on load
   - All filter changes fetch /api/v1/products/ and swap the grid in place
   - URL updates via history.pushState — back/forward work
   - Infinite scroll via IntersectionObserver
   - Grid/list toggle persisted in localStorage
   ========================================================================= */
(function () {
    const page = document.getElementById('product-list-page');
    if (!page) return;

    const API = '/api/v1/products/';

    // -------- Elements --------
    const grid = document.getElementById('pl-grid');
    const loader = document.getElementById('pl-loader');
    const endNote = document.getElementById('pl-end');
    const countEl = document.getElementById('pl-count');
    const searchInput = document.getElementById('pl-search-input');
    const sortSelect = document.getElementById('pl-sort');
    const minPrice = document.getElementById('pl-min-price');
    const maxPrice = document.getElementById('pl-max-price');
    const inStock = document.getElementById('pl-in-stock');
    const clearBtn = document.getElementById('pl-clear');
    const chipsEl = document.getElementById('pl-chips');
    const sidebar = document.getElementById('pl-sidebar');
    const sidebarToggle = document.getElementById('pl-filters-toggle');
    const sidebarClose = document.getElementById('pl-sidebar-close');
    const sidebarBackdrop = document.getElementById('pl-sidebar-backdrop');
    const viewGridBtn = document.getElementById('view-grid');
    const viewListBtn = document.getElementById('view-list');
    const sentinel = document.getElementById('pl-sentinel');

    if (!grid) return;

    // -------- State --------
    let nextPage = 2;
    let hasNext = true;
    let isLoading = false;

    // -------- Read filters from the URL --------
    function readUrlFilters() {
        const p = new URLSearchParams(window.location.search);
        return {
            q: p.get('q') || '',
            department: p.get('department') || '',
            category: p.get('category') || '',
            type: p.get('type') || 'all',
            in_stock: p.get('in_stock') === '1' ? '1' : '',
            min_price: p.get('min_price') || '',
            max_price: p.get('max_price') || '',
            sort: p.get('sort') || 'newest',
        };
    }

    // -------- Write filters to the URL --------
    function writeUrlFilters(filters, push) {
        const p = new URLSearchParams();
        Object.entries(filters).forEach(([k, v]) => {
            if (v && !(k === 'type' && v === 'all') && !(k === 'sort' && v === 'newest')) {
                p.set(k, v);
            }
        });
        const qs = p.toString();
        const url = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
        if (push) history.pushState({ filters }, '', url);
    }

    // -------- Card renderer (from JSON) --------
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function cardHTML(p) {
        const img = p.image
            ? `<img src="${esc(p.image)}" alt="${esc(p.name)}">`
            : `<div class="img-placeholder"><svg viewBox="0 0 24 24" style="color:#45D68A"><use href="#icon-bottle"/></svg></div>`;

        let priceHtml;
        if (p.is_rentable) {
            const dr = p.daily_rate ? Number(p.daily_rate).toLocaleString() : '—';
            const dep = p.rental_deposit && Number(p.rental_deposit) > 0
                ? `<p class="product-deposit-note">+ KES ${Number(p.rental_deposit).toLocaleString()} deposit</p>`
                : '';
            priceHtml = `<p class="price">KES ${dr}<span class="price-unit">/day</span></p>${dep}`;
        } else if (p.price_range && p.price_range[0] !== p.price_range[1]) {
            priceHtml = `<p class="price">KES ${esc(p.price_range[0])}–${esc(p.price_range[1])}</p>`;
        } else {
            priceHtml = `<p class="price">KES ${esc(p.price)}</p>`;
        }

        const badge = p.is_rentable
            ? `<span class="badge start-from-badge">For Hire</span>`
            : '';

        const cta = p.is_rentable
            ? `<a href="/rent/${p.id}/" class="btn btn-small btn-quick">Rent this car</a>`
            : p.in_stock
                ? `<form action="/store/cart/add/${p.id}/" method="post" class="ajax-add-form product-quick-add">
                     <input type="hidden" name="quantity" value="1">
                     <input type="hidden" name="next" value="${esc(window.location.pathname)}">
                     <button type="submit" class="btn btn-small btn-quick">Add to cart</button>
                   </form>`
                : '';

        const tag = p.is_rentable
            ? `<span class="product-tag tag-success">Available for hire</span>`
            : p.in_stock
                ? `<span class="product-tag tag-success">In stock</span>`
                : `<span class="product-tag tag-muted">Out of stock</span>`;

        const vendor = p.vendor_name
            ? `<p class="product-vendor">by ${esc(p.vendor_name)}</p>`
            : '';

        return `
            <article class="product-card" data-product-id="${p.id}">
                <a href="/product/${esc(p.slug)}/">
                    ${img}
                    ${badge}
                </a>
                ${priceHtml}
                <a href="/product/${esc(p.slug)}/" class="product-title-link">
                    <h4 class="product-title">${esc(p.name)}</h4>
                </a>
                ${vendor}
                <div class="product-meta-row">${tag}</div>
                ${cta}
            </article>
        `;
    }

    // -------- Fetch products --------
    async function fetchProducts(filters, page, append) {
        const p = new URLSearchParams();
        Object.entries(filters).forEach(([k, v]) => {
            if (v && !(k === 'type' && v === 'all') && !(k === 'sort' && v === 'newest')) {
                p.set(k, v);
            }
        });
        if (page > 1) p.set('page', page);

        const url = API + (p.toString() ? '?' + p.toString() : '');
        isLoading = true;
        loader.style.display = 'flex';

        try {
            const res = await fetch(url, {
                headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
            });
            if (!res.ok) throw new Error('API error');
            const data = await res.json();

            const cards = (data.results || []).map(cardHTML).join('');

            if (append) {
                grid.insertAdjacentHTML('beforeend', cards || '');
            } else {
                grid.innerHTML = cards || `<p class="product-grid-empty">No products match your filters.</p>`;
            }

            const total = data.count != null ? data.count : 0;
            if (countEl) countEl.textContent = `${total} product${total === 1 ? '' : 's'}`;
            hasNext = !!data.next;
            nextPage = page + 1;
            endNote.style.display = hasNext ? 'none' : (append ? 'block' : 'none');
        } catch (err) {
            console.warn('Product fetch failed', err);
        } finally {
            isLoading = false;
            loader.style.display = 'none';
        }
    }

    // -------- Active filter chips --------
    function renderChips(filters) {
        if (!chipsEl) return;
        const chips = [];
        if (filters.q) chips.push(['q', `Search: "${filters.q}"`]);
        if (filters.department) chips.push(['department', `Dept: ${filters.department}`]);
        if (filters.category) chips.push(['category', `Category: ${filters.category}`]);
        if (filters.type && filters.type !== 'all') chips.push(['type', `Type: ${filters.type}`]);
        if (filters.in_stock) chips.push(['in_stock', `In stock only`]);
        if (filters.min_price) chips.push(['min_price', `Min: ${filters.min_price}`]);
        if (filters.max_price) chips.push(['max_price', `Max: ${filters.max_price}`]);

        if (!chips.length) { chipsEl.innerHTML = ''; return; }

        chipsEl.innerHTML = chips.map(([key, label]) =>
            `<button type="button" class="pl-chip" data-chip="${key}">${label} <span aria-hidden="true">×</span></button>`
        ).join('');
    }

    // -------- Apply a filter change --------
    function applyChange(newFilters) {
        const filters = { ...readUrlFilters(), ...newFilters };
        writeUrlFilters(filters, true);
        renderChips(filters);
        fetchProducts(filters, 1, false);
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    // -------- Sidebar toggle (mobile) --------
    function openSidebar() { sidebar.classList.add('is-open'); sidebarBackdrop.classList.add('is-open'); }
    function closeSidebar() { sidebar.classList.remove('is-open'); sidebarBackdrop.classList.remove('is-open'); }
    if (sidebarToggle) sidebarToggle.addEventListener('click', openSidebar);
    if (sidebarClose) sidebarClose.addEventListener('click', closeSidebar);
    if (sidebarBackdrop) sidebarBackdrop.addEventListener('click', closeSidebar);

    // -------- Department / category / type links --------
    page.addEventListener('click', (e) => {
        const link = e.target.closest('[data-filter]');
        if (!link) return;
        e.preventDefault();
        const key = link.dataset.filter;
        const value = link.dataset.value;
        applyChange({ [key]: value, page: '' });

        // Update the active class within that group
        const group = link.closest('ul');
        if (group) group.querySelectorAll('a').forEach(a => a.classList.remove('is-active'));
        link.classList.add('is-active');
    });

    // -------- Sort --------
    if (sortSelect) {
        sortSelect.value = readUrlFilters().sort;
        sortSelect.addEventListener('change', () => applyChange({ sort: sortSelect.value }));
    }

    // -------- In-stock toggle --------
    if (inStock) {
        inStock.addEventListener('change', () => applyChange({ in_stock: inStock.checked ? '1' : '' }));
    }

    // -------- Price (debounced) --------
    let priceTimer;
    function debouncedPrice() {
        clearTimeout(priceTimer);
        priceTimer = setTimeout(() => {
            applyChange({
                min_price: minPrice.value.trim(),
                max_price: maxPrice.value.trim(),
            });
        }, 450);
    }
    if (minPrice) minPrice.addEventListener('input', debouncedPrice);
    if (maxPrice) maxPrice.addEventListener('input', debouncedPrice);

    // -------- Search (debounced) --------
    let searchTimer;
    if (searchInput) {
        searchInput.value = readUrlFilters().q;
        searchInput.addEventListener('input', () => {
            clearTimeout(searchTimer);
            searchTimer = setTimeout(() => applyChange({ q: searchInput.value.trim() }), 350);
        });
    }

    // -------- Clear all --------
    if (clearBtn) {
        clearBtn.addEventListener('click', () => {
            history.pushState({}, '', window.location.pathname);
            window.location.reload();
        });
    }

    // -------- Chip removal --------
    if (chipsEl) {
        chipsEl.addEventListener('click', (e) => {
            const chip = e.target.closest('[data-chip]');
            if (!chip) return;
            const key = chip.dataset.chip;
            const clear = { [key]: '' };
            applyChange(clear);
            // Sync UI elements
            if (key === 'q' && searchInput) searchInput.value = '';
            if (key === 'min_price' && minPrice) minPrice.value = '';
            if (key === 'max_price' && maxPrice) maxPrice.value = '';
            if (key === 'in_stock' && inStock) inStock.checked = false;
            if (key === 'type' && document.querySelector('[data-filter="type"][data-value="all"]')) {
                document.querySelectorAll('#pl-type a').forEach(a => a.classList.remove('is-active'));
                document.querySelector('[data-filter="type"][data-value="all"]').classList.add('is-active');
            }
        });
    }

    // -------- View toggle (grid / list) --------
    function applyView(mode) {
        if (mode === 'list') {
            grid.classList.add('is-list');
            if (viewListBtn) viewListBtn.classList.add('is-active');
            if (viewGridBtn) viewGridBtn.classList.remove('is-active');
        } else {
            grid.classList.remove('is-list');
            if (viewGridBtn) viewGridBtn.classList.add('is-active');
            if (viewListBtn) viewListBtn.classList.remove('is-active');
        }
        try { localStorage.setItem('pl-view', mode); } catch (e) {}
    }
    const savedView = (() => { try { return localStorage.getItem('pl-view'); } catch (e) { return null; } })() || 'grid';
    applyView(savedView);
    if (viewGridBtn) viewGridBtn.addEventListener('click', () => applyView('grid'));
    if (viewListBtn) viewListBtn.addEventListener('click', () => applyView('list'));

    // -------- Infinite scroll --------
    if ('IntersectionObserver' in window && sentinel) {
        const io = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                if (entry.isIntersecting && hasNext && !isLoading) {
                    fetchProducts(readUrlFilters(), nextPage, true);
                }
            });
        }, { rootMargin: '400px 0px' });
        io.observe(sentinel);
    }

    // -------- Back / forward --------
    window.addEventListener('popstate', () => {
        const filters = readUrlFilters();
        if (searchInput) searchInput.value = filters.q;
        if (sortSelect) sortSelect.value = filters.sort;
        if (minPrice) minPrice.value = filters.min_price;
        if (maxPrice) maxPrice.value = filters.max_price;
        if (inStock) inStock.checked = filters.in_stock === '1';
        renderChips(filters);
        fetchProducts(filters, 1, false);
    });

    // -------- Initial chips --------
    renderChips(readUrlFilters());
})();
