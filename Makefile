.PHONY: install uninstall install-collector install-extension uninstall-collector uninstall-extension test test-collector test-extension

install: install-collector install-extension

install-collector:
	./scripts/install-collector.sh

install-extension:
	./scripts/install-extension.sh

uninstall: uninstall-extension uninstall-collector

uninstall-collector:
	./scripts/uninstall-collector.sh

uninstall-extension:
	./scripts/uninstall-extension.sh

test: test-collector test-extension

test-collector:
	cd collector && python3 -m unittest discover -s tests -v

test-extension:
	node extension/tests/run.js
