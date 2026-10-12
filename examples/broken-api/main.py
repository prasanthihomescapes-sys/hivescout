import traceback

from fastapi import FastAPI, HTTPException

app = FastAPI()


def remaining_budget(tenant: str) -> float:
    try:
        return lookup(tenant)
    except KeyError:
        return 100.0  # invented number shown as real


def lookup(tenant: str) -> float:
    raise KeyError(tenant)


@app.get("/price")
def price(sku: str):
    try:
        return {"price": compute(sku)}
    except ValueError as e:
        logger.warning("bad sku %s", sku)  # logger never defined -> NameError -> bare 500
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/report")
def report():
    try:
        return build()
    except Exception:
        pass


@app.get("/debug")
def debug():
    try:
        return build()
    except Exception:
        raise HTTPException(status_code=500, detail=traceback.format_exc())


def compute(sku: str) -> float:
    return float(sku)


def build() -> dict:
    return {}
