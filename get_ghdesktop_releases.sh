#!/bin/sh

releases="$1"

ghdesktop_releases="https://central.github.com/deployments/desktop/desktop/changelog.json"

die () {
	printf "%s\n" "$@"
	exit 1
}

if test -z "$releases"
then
	tmpdir=$(mktemp -d) || die "Failed to create temp dir using mktemp."

	releases="$tmpdir/ghdesktop_releases.json"

	wget "$ghdesktop_releases" -O "$releases" ||
		die "Failed to wget '$ghdesktop_releases'."
fi

perl -ne '@a = m/"version":"([\d.]+)"/gsm;
     map { print "[$_](https://desktop.github.com/release-notes/)\n"; } @a;' "$releases"
