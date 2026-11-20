from autotrade.book import OrderBook, Stamper


def test_book_best_and_delete():
    b = OrderBook(1)
    assert b.best() == (None, None)
    assert b.apply([(1, "bid", 100, 5), (2, "bid", 101, 5), (3, "ask", 103, 5), (4, "ask", 102, 1)]) == 1
    assert b.best() == (101, 102)
    assert b.snapshot() == ([(101, 5), (100, 5)], [(102, 1), (103, 5)])
    b.apply([], [2, 4])
    assert b.best() == (100, 103)
    b.apply([(1, "bid", 99, 7)])  # same id replaced
    assert b.best() == (99, 103)
    b.apply([], [99999])  # unknown id ignored
    b.apply([], [1, 3])
    assert b.best() == (None, None)


def test_delete_then_add_in_one_event_keeps_new_quote():
    b = OrderBook(1)
    b.apply([(1, "bid", 100, 1), (2, "ask", 102, 1)])
    b.apply([(1, "bid", 101, 1)], [1])  # collector order: deletions first, then new quotes
    assert b.best() == (101, 102)


def test_stamper_skips_until_spot_then_stamps_latest_spot():
    s = Stamper()
    assert s.on_depth(1, [(1, "bid", 100, 1), (2, "ask", 102, 1)]) is None
    s.on_spot(1, 5000)
    r = s.on_depth(1, [(3, "bid", 101, 1)])  # book kept from the skipped event
    assert (r.symbol_id, r.stamp_ms, r.best_bid, r.best_ask, r.source) == (1, 5000, 101, 102, "live")
    s.on_spot(1, 6000)
    assert s.on_depth(1, [], [3]).stamp_ms == 6000
    assert s.on_depth(2, [(1, "bid", 1, 1)]) is None  # spot is per symbol
