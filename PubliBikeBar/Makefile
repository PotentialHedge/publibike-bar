APP      := build/PubliBike.app
DEST     := /Applications
VERSION  := $(shell /usr/libexec/PlistBuddy -c 'Print CFBundleShortVersionString' Info.plist)
ZIP      := build/PubliBike-$(VERSION).zip

.PHONY: build app install run dist clean

# Local build (this Mac's architecture only).
build:
	swift build -c release

app: build
	$(call bundle,$(shell swift build -c release --show-bin-path))

install: app
	-pkill -x PubliBikeBar
	while pgrep -x PubliBikeBar >/dev/null; do sleep 0.2; done
	mkdir -p $(DEST)
	rm -rf $(DEST)/PubliBike.app
	cp -R $(APP) $(DEST)/
	open $(DEST)/PubliBike.app

run: app
	-pkill -x PubliBikeBar
	while pgrep -x PubliBikeBar >/dev/null; do sleep 0.2; done
	open $(APP)

# Universal (Apple Silicon + Intel) zip for GitHub releases / the Homebrew cask.
dist:
	swift build -c release --arch arm64 --arch x86_64
	$(call bundle,$(shell swift build -c release --arch arm64 --arch x86_64 --show-bin-path))
	rm -f $(ZIP)
	ditto -c -k --keepParent $(APP) $(ZIP)
	@echo "$(ZIP)"
	@shasum -a 256 $(ZIP)

clean:
	rm -rf .build build

define bundle
	rm -rf $(APP)
	mkdir -p $(APP)/Contents/MacOS
	cp $(1)/PubliBikeBar $(APP)/Contents/MacOS/PubliBikeBar
	cp Info.plist $(APP)/Contents/Info.plist
	codesign --force --sign - $(APP)
endef
