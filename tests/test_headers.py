"""Header parsing: addresses that Python's parser drops are kept and logged, never lost silently."""
from src import ingest

from conftest import email


def test_malformed_addresses_are_recovered_and_logged(make_corpus):
    # Shapes taken from the corpus (farmer-d/logistics/124, giron-d/inbox/292).
    cc = 'sl <".\'"wehner@enron.com>, gary.lamphier@enron.com'
    to = "b@enron.com, \r\n\t/o=enron/ou=na/cn=recipients/cn=notesaddr/cn=41bc9f29-eb12503a@enron.com"
    corpus = make_corpus({"mb/inbox/1": email(to=to, cc=cc)})
    r = ingest.build(corpus)
    m = r.messages["mb/inbox/1"]
    assert "wehner@enron.com" in m.cc and "gary.lamphier@enron.com" in m.cc
    assert "41bc9f29-eb12503a@enron.com" in m.to and "b@enron.com" in m.to
    rec = [x for x in r.readings if x.rule == "address_recovered"]
    assert {x.read_as for x in rec} == {"wehner@enron.com", "41bc9f29-eb12503a@enron.com"}
    for x in rec:
        assert m.text[x.start:x.end] == x.written                      # points at the raw header text
        assert x.certainty == "inferred" and "malformed" in x.why
    assert all(r.reconciliation().values())


def test_clean_headers_log_nothing(make_corpus):
    r = ingest.build(make_corpus({"mb/inbox/1": email(to="b@enron.com, c@enron.com", cc="d@enron.com")}))
    assert [x for x in r.readings if x.rule == "address_recovered"] == []
