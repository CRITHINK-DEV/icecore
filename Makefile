.PHONY: app demo-data rich-demo

# Run the app on your laptop (docs/app.md). Set ICECORE_VIEWS_SCHEMA and a Snowflake login first.
app:
	scripts/run_app_locally.sh

# Fill a persistent demo schema so every page has content (docs/app.md).
demo-data:
	scripts/demo_data.sh

# A richer demo (15 real runs, about 30 minutes; sandbox only, see the header of the script).
rich-demo:
	scripts/demo/build_rich_demo.sh
