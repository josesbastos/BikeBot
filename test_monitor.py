import os

from bs4 import BeautifulSoup

from monitor import (
    Offer,
    canonicalize_url,
    choose_price,
    extract_prices,
    load_local_env,
    page_availability,
    parse_price_number,
    product_identity_text,
    should_alert,
    size_evidence_text,
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


def test_canonical_url_removes_tracking_but_keeps_product_options():
    url = "https://www.shop.pt/bike?size=L&utm_source=search&color=red#details"
    assert canonicalize_url(url) == "https://www.shop.pt/bike?size=L&color=red"


def make_offer(price=1699.0, availability="in_stock"):
    return Offer(
        model="Example Bike",
        size="L",
        price=price,
        title="Example Bike",
        url="https://shop.pt/bike",
        domain="shop.pt",
        availability=availability,
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
