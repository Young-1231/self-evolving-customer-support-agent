.PHONY: demo evolve test plot clean

demo:        ## before/after learning on one hard ticket (zero deps)
	python -m seagent.cli demo

evolve:      ## run the full self-evolution experiment (mock backend)
	python scripts/run_experiment.py

test:        ## run the offline test suite
	cd $(CURDIR) && PYTHONPATH=src python -m pytest -q tests

plot:        ## render evolution_curve.png from the latest metrics.json
	python scripts/plot_evolution.py experiments/metrics.json

clean:
	rm -rf experiments/*.json experiments/*.png experiments/*.md __pycache__ .pytest_cache
