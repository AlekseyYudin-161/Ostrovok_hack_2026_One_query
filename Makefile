# Корневой Makefile только подключает цели модулей. Свои цели — в make/<модуль>.mk
include make/*.mk
.DEFAULT_GOAL := help
.PHONY: help
help:
	@grep -hE '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'
