import os

from bs4 import BeautifulSoup
import monitor

from monitor import (
    Offer,
    canonicalize_url,
    classify_seller,
    choose_price,
    extract_prices,
    extract_listed_sizes,
    extract_model_year,
    is_qualifying_offer,
    load_local_env,
    olx_advert_offers,
    page_availability,
    parse_price_number,
    prune_non_target_marketplace_state,
    product_identity_text,
    should_alert,
    send_no_match_email,
    size_evidence_text,
    summary_rows,
    target_size_matches,
    update_state,
    visible_text,
)

def test_european_price():
    assert parse_price_number("1.699,00") == 1699.0

def test_simple_euro():
    vals = extract_prices("Preço: 1.749 €", min_price=900)
    assert vals == [1749.0]

def test_us_style():
    assert parse_price_number("1,699.00") == 1699.0


def test_prices_with_currency_before_or_after_number():
    text = "1.749,00 € | EUR 1699.00 | 1,799.00 EUR"
    assert extract_prices(text, min_price=900) == [1699.0, 1749.0, 1799.0]


def test_visible_text_does_not_destroy_structured_product_data():
    markup = """
    <html><head><script type="application/ld+json">
    {"@type":"Offer","price":"1699.00","availability":"https://schema.org/InStock"}
    </script></head><body><h1>Bike</h1><p>Size L</p></body></html>
    """
    soup = BeautifulSoup(markup, "html.parser")
    text = visible_text(soup)
    price, source, availability = choose_price(markup, text, "", soup, 900)

    assert "Size L" in text
    assert price == 1699.0
    assert source == "structured_data"
    assert availability == "in_stock"


def test_product_stock_label_marks_page_out_of_stock():
    soup = BeautifulSoup(
        '<html><h1>Example Bike</h1><div class="product-stock">Esgotado</div></html>',
        "html.parser",
    )
    assert page_availability(soup, "unknown", ["esgotado"]) == "out_of_stock"


def test_collection_body_does_not_count_as_product_identity():
    soup = BeautifulSoup(
        "<html><title>Orbea bikes</title><h1>Orbea</h1>"
        "<div>Recommended: Orbea Orca M30</div></html>",
        "html.parser",
    )
    assert not product_identity_text(soup).lower().endswith("orca m30")


def test_standalone_size_option_gets_variant_context():
    soup = BeautifulSoup(
        '<html><select id="bike-size"><option value="L">L</option></select></html>',
        "html.parser",
    )
    evidence = size_evidence_text(soup, "", "", "")
    assert target_size_matches(evidence, ["L"], ["out of stock"]) == ["L"]


def test_marketplace_frame_size_ignores_wheel_size():
    text = "Tamanho de roda: 28 Estado: Usado Tamanho: M/L Descrição"
    assert extract_listed_sizes(text) == ["M", "L"]


def test_canonical_url_removes_tracking_but_keeps_product_options():
    url = "https://www.shop.pt/bike?size=L&utm_source=search&color=red#details"
    assert canonicalize_url(url) == "https://www.shop.pt/bike?size=L&color=red"


def test_canonical_url_normalizes_mobile_olx_links():
    assert (
        canonicalize_url("https://m.olx.pt/d/anuncio/bike-ID123.html?utm_source=x")
        == "https://www.olx.pt/d/anuncio/bike-ID123.html"
    )


def make_offer(price=1699.0, availability="in_stock"):
    return Offer(
        model="Example Bike",
        size="L",
        year=2025,
        price=price,
        title="Example Bike",
        url="https://shop.pt/bike",
        domain="shop.pt",
        availability=availability,
        seller_type="store",
        target_size_match=True,
        source="structured_data",
    )


def test_dry_run_state_remains_eligible_for_a_real_alert():
    state = {"offers": {}}
    offer = make_offer()
    update_state(offer, state, alerted=False)
    assert should_alert(offer, state)


def test_price_drop_is_measured_from_last_alerted_price():
    state = {"offers": {}}
    update_state(make_offer(1500), state, alerted=True)
    update_state(make_offer(1700), state, alerted=False)

    assert not should_alert(make_offer(1600), state)
    assert should_alert(make_offer(1499), state)


def test_return_to_stock_alerts_again():
    state = {"offers": {}}
    update_state(make_offer(), state, alerted=True)
    update_state(make_offer(availability="out_of_stock"), state, alerted=False)
    assert should_alert(make_offer(availability="in_stock"), state)


def test_local_env_loads_values_without_overriding_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'RESEND_API_KEY="from-file"\nRESEND_FROM=Bike Alert <test@example.com>\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEND_API_KEY", "from-shell")
    monkeypatch.delenv("RESEND_FROM", raising=False)

    load_local_env(env_file)

    assert os.environ["RESEND_API_KEY"] == "from-shell"
    assert os.environ["RESEND_FROM"] == "Bike Alert <test@example.com>"


class FakeResponse:
    def __init__(self, status_code, text, content_type="text/html; charset=UTF-8"):
        self.status_code = status_code
        self.text = text
        self.headers = {"content-type": content_type}


def test_fetch_page_uses_browser_fallback_after_403(monkeypatch):
    denied = FakeResponse(403, "<html><h1>Denied</h1></html>")
    product = FakeResponse(200, "<html><title>Bike</title><h1>Bike</h1></html>")

    monkeypatch.setattr(monitor.HTTP_SESSION, "get", lambda *args, **kwargs: denied)

    class FakeBrowserClient:
        def __init__(self, **kwargs):
            self.urls = []

        def get(self, url):
            self.urls.append(url)
            return product

    monkeypatch.setattr(monitor.primp, "Client", FakeBrowserClient)
    text, soup = monitor.fetch_page("https://shop.example/bike")

    assert "<h1>Bike</h1>" in text
    assert soup.h1.get_text() == "Bike"


def test_search_uses_second_backend_when_first_fails(monkeypatch):
    calls = []

    class FakeSearchClient:
        def __init__(self, **kwargs):
            pass

        def text(self, query, **kwargs):
            calls.append(kwargs["backend"])
            if kwargs["backend"] == "bing":
                raise RuntimeError("temporary failure")
            return [{"title": "Bike", "href": "https://shop.example/bike"}]

    monkeypatch.setattr(monitor, "DDGS", FakeSearchClient)
    results = monitor.search_web("bike", 10, ["bing", "yahoo"])

    assert calls == ["bing", "yahoo"]
    assert results[0]["title"] == "Bike"


def test_summary_merges_sizes_for_the_same_shop_product():
    offers = [make_offer(2200), make_offer(2200)]
    offers[1].size = "XL"

    rows = summary_rows(offers)

    assert len(rows) == 1
    assert rows[0]["domain"] == "shop.pt"
    assert rows[0]["price"] == 2200
    assert rows[0]["sizes"] == ["L", "XL"]


def test_no_match_email_contains_grouped_prices(monkeypatch):
    captured = {}

    class SuccessfulResponse:
        status_code = 200
        text = "ok"

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return SuccessfulResponse()

    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(monitor.requests, "post", fake_post)
    send_no_match_email([make_offer(2200)], "recipient@example.com", 1800)

    payload = captured["json"]
    assert payload["to"] == ["recipient@example.com"]
    assert "Sem ofertas até 1800 €" in payload["subject"]
    assert "shop.pt" in payload["html"]
    assert "2.200,00 €" in payload["html"]
    assert "Ano: 2025" in payload["html"]
    assert "Lojas e vendedores profissionais" in payload["html"]
    assert "Nenhum anúncio ativo" in payload["html"]


def test_model_year_prefers_product_identity_and_ignores_publication_date():
    assert extract_model_year("Trek Domane 2024", "Publicado em 2026", 2026) == 2024
    assert (
        extract_model_year(
            "Giant Defy Advanced",
            "Publicado em 2026. Bicicleta modelo de 2021.",
            2026,
        )
        == 2021
    )
    assert extract_model_year("Giant Defy Advanced", "Publicado em 2026", 2026) is None
    assert (
        extract_model_year(
            "Orbea Orca M30",
            "Características Cuadro: Orbea Orca carbon OMR 2024 Horquilla carbono",
            2026,
        )
        == 2024
    )


def test_marketplace_sellers_are_split_between_private_and_professional():
    assert classify_seller("olx.pt", "Particular Estado: Usado", ["olx.pt"]) == "private"
    assert (
        classify_seller("olx.pt", "Profissional Estado: Novo", ["olx.pt"])
        == "professional"
    )
    assert classify_seller("bikezone.pt", "", ["olx.pt"]) == "store"


def test_summary_excludes_non_target_private_sellers():
    store_offer = make_offer(2200)
    private_offer = make_offer(1600)
    private_offer.domain = "olx.pt"
    private_offer.url = "https://olx.pt/d/anuncio/bike-ID123.html"
    private_offer.seller_type = "private"
    private_offer.target_size_match = False

    rows = summary_rows([private_offer, store_offer])

    assert [row["seller_type"] for row in rows] == ["store"]


def test_non_target_marketplace_offer_is_not_reported_or_qualified():
    offer = make_offer(1500)
    offer.domain = "olx.pt"
    offer.seller_type = "private"
    offer.target_size_match = False

    assert summary_rows([offer]) == []
    assert not is_qualifying_offer(offer, 1800)


def make_olx_advert(size="L", year=2025, business=False):
    return {
        "id": 123,
        "status": "active",
        "url": "https://www.olx.pt/d/anuncio/giant-defy-ID123.html",
        "title": f"Giant Defy Advanced 2 {year}",
        "description": f"Bicicleta tamanho {size}, como nova",
        "business": business,
        "params": [
            {
                "key": "price",
                "value": {"value": 1599, "currency": "EUR"},
            },
            {"key": "size", "value": {"key": size.lower(), "label": size}},
        ],
    }


def test_olx_api_advert_is_converted_when_all_rules_match():
    offers = olx_advert_offers(
        make_olx_advert(),
        "Giant Defy Advanced",
        ["Giant Defy Advanced"],
        ["L", "XL"],
        2023,
        300,
    )

    assert len(offers) == 1
    assert offers[0].size == "L"
    assert offers[0].year == 2025
    assert offers[0].price == 1599
    assert offers[0].source == "olx_api"
    assert offers[0].seller_type == "private"


def test_olx_api_advert_is_rejected_for_wrong_size_or_old_year():
    common = (
        "Giant Defy Advanced",
        ["Giant Defy Advanced"],
        ["L", "XL"],
        2023,
        300,
    )
    assert olx_advert_offers(make_olx_advert(size="M"), *common) == []
    assert olx_advert_offers(make_olx_advert(year=2022), *common) == []


def test_compact_olx_frame_size_notation_is_supported():
    assert extract_listed_sizes("T:58 Quadro(60)") == ["58", "60"]


def test_old_non_target_olx_state_is_removed():
    state = {
        "offers": {
            "wrong": {"domain": "olx.pt", "target_size_match": False},
            "right": {"domain": "olx.pt", "target_size_match": True},
            "shop": {"domain": "shop.pt", "target_size_match": False},
        }
    }

    assert prune_non_target_marketplace_state(state, ["olx.pt"]) == 1
    assert set(state["offers"]) == {"right", "shop"}


def test_portuguese_search_covers_each_shop_and_filters_wrong_domains(monkeypatch):
    calls = []

    def search(query, limit, backends, **kwargs):
        calls.append((query, limit))
        domain = query.split()[0].removeprefix("site:")
        return [
            {"href": f"https://{domain}/product/bike", "title": "Orbea Orca M30"},
            {"href": "https://unrelated.example/bike", "title": "Orbea Orca M30"},
        ]

    monkeypatch.setattr(monitor, "search_web", search)
    cfg = {
        "allowed_domains": ["gaiabike.pt", "viabike.pt"],
        "portuguese_search_domains": ["gaiabike.pt", "viabike.pt", "unapproved.pt"],
        "max_results_per_store": 10,
    }
    hits = monitor.search_portuguese_stores(["Orbea Orca M30"], cfg)
    assert len(calls) == 2
    assert {hit["href"] for hit in hits} == {
        "https://gaiabike.pt/product/bike", "https://viabike.pt/product/bike"
    }
    assert all(limit == 10 for _, limit in calls)


def test_catalog_discovers_only_same_site_product_pages(monkeypatch):
    soup = BeautifulSoup('''
        <a href="/product/bike">Orbea Orca M30</a>
        <a href="/product/bike">Orbea Orca M30</a>
        <a href="/cart/add/bike">Orbea Orca M30</a>
        <a href="https://elsewhere.pt/product/bike">Orbea Orca M30</a>
        <a href="/product/helmet">Helmet</a>
    ''', "html.parser")
    monkeypatch.setattr(monitor, "fetch_page", lambda url: (str(soup), soup))
    cfg = {
        "allowed_domains": ["gaiabike.pt"],
        "catalog_searches": [{
            "url": "https://www.gaiabike.pt/search?q={query}",
            "product_path": "/product/",
        }],
    }
    hits = monitor.search_portuguese_stores(["Orbea Orca M30"], cfg)
    assert [hit["href"] for hit in hits] == ["https://www.gaiabike.pt/product/bike"]
