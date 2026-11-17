from datetime import datetime, timedelta


def msk_date(ts: str) -> str:
    return (datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S") + timedelta(hours=3)).strftime("%d.%m")
