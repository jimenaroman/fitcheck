"""Model prices in US dollars per million tokens. Update when Anthropic or Google change prices."""

PRICES_PER_MILLION = {
    # model: (input, output)
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-opus-5-5": (4.00, 20.00),
    # Paid tier, through 2026-12-31. Google doubles it to (1.50, 7.50) on 2027-01-01: update then.
    "gemini-3.8-flash": (0.75, 3.75),
    "fake": (0.00, 0.00),
}


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Dollar cost of one call. Unknown models raise, so a missing price can't skip the budget."""
    if model not in PRICES_PER_MILLION:
        raise KeyError(f"no price for model {model!r}; add it to PRICES_PER_MILLION in pricing.py")
    input_price, output_price = PRICES_PER_MILLION[model]
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000
