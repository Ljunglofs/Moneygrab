"""Minimal ersättning för streamlit i backtestet: sok_module använder bara
st.cache_data som dekorator. Ingen cache här — varje anrop räknas om."""
def cache_data(*a, **k):
    if a and callable(a[0]) and not k:
        return a[0]
    return lambda f: f
