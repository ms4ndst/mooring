import os
import time

from mooring.services.contacts import ContactBook, number_key, parse_vcard

CARD = (
    "BEGIN:VCARD\r\nVERSION:2.1\r\nFN:Alex Ex\r\n ample\r\n"
    "TEL;CELL:+46 70-000 00 01\r\nTEL;HOME:tel:08-555 000 02\r\nEND:VCARD\r\n"
)


def test_parse_vcard_unfolds_and_reads_numbers():
    name, numbers = parse_vcard(CARD)
    assert name == "Alex Example"
    assert numbers == ["+46 70-000 00 01", "08-555 000 02"]


def test_number_key_ignores_country_prefix_and_formatting():
    assert number_key("+46 70-000 00 01") == number_key("070 000 00 01")


def test_contact_book_lookup_and_reload(tmp_path):
    (tmp_path / "a.vcf").write_text(CARD)
    book = ContactBook(tmp_path)
    assert book.name_for("0700000001") == "Alex Example"
    assert book.name_for("+46700000099") is None
    assert book.display_name(("+46700000001", "+46700000099")) == "Alex Example, +46700000099"

    (tmp_path / "b.vcf").write_text("BEGIN:VCARD\nFN:Sam Sample\nTEL:+46700000099\nEND:VCARD\n")
    later = time.time() + 5
    os.utime(tmp_path / "b.vcf", (later, later))
    assert book.name_for("+46700000099") == "Sam Sample"


def test_missing_directory_is_fine(tmp_path):
    book = ContactBook(tmp_path / "nope")
    assert book.display_name(("+46700000001",)) == "+46700000001"


def test_quoted_printable_name_with_soft_line_break():
    # "Åsa Öberg" as Android exports it (vCard 2.1, UTF-8, quoted-printable)
    card = (
        "BEGIN:VCARD\r\nVERSION:2.1\r\n"
        "N;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=C3=96berg;=C3=85sa;;;\r\n"
        "FN;CHARSET=UTF-8;ENCODING=QUOTED-PRINTABLE:=C3=85sa=20=\r\n"
        "=C3=96berg\r\n"
        "TEL;CELL:+46700000003\r\nEND:VCARD\r\n"
    )
    assert parse_vcard(card) == ("Åsa Öberg", ["+46700000003"])


def test_base64_name():
    import base64

    encoded = base64.b64encode("Zoë Test".encode()).decode()
    card = f"BEGIN:VCARD\nFN;ENCODING=BASE64;CHARSET=UTF-8:{encoded}\nTEL:+1\nEND:VCARD\n"
    assert parse_vcard(card)[0] == "Zoë Test"


def test_structured_name_fallback_when_no_fn():
    card = "BEGIN:VCARD\nVERSION:2.1\nN:Fixture;Kim;;;\nTEL:+46700000005\nEND:VCARD\n"
    assert parse_vcard(card)[0] == "Kim Fixture"


def test_unknown_charset_falls_back_to_utf8():
    card = "BEGIN:VCARD\nFN;CHARSET=X-NOPE;ENCODING=QUOTED-PRINTABLE:Bo=20B\nEND:VCARD\n"
    assert parse_vcard(card)[0] == "Bo B"
