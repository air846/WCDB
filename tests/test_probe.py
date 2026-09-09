from tools.probe_wechat import summarize


def test_summarize_counts():
    res = {"accounts": [{"wxid": "wxid_abc", "db_files": [
        {"name": "message_0.db", "decrypted": True, "tables": 3},
        {"name": "message_1.db", "decrypted": False},
    ]}]}
    s = summarize(res)
    assert s["decrypted_dbs"] == 1
    assert s["total_dbs"] == 2
