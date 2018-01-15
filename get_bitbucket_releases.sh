#!/bin/sh

releases="$1"

bitbucket_releases="https://confluence.atlassian.com/bitbucketserver/bitbucket-server-5-release-notes-892802659.html"

die () {
	printf "%s\n" "$@"
	exit 1
}

if test -z "$releases"
then
	tmpdir=$(mktemp -d) || die "Failed to create temp dir using mktemp."

	releases="$tmpdir/bitbucket_releases.html"

	wget "$bitbucket_releases" -O "$releases" ||
		die "Failed to wget '$bitbucket_releases'."
fi

perl -ne '@a = m/href="(\/bitbucketserver\/bitbucket-server-[\d-]+-release-notes-[\d]+.html)">Bitbucket Server ([^<]+) release notes</gsm;
     while (my ($u, $v) = splice (@a, 0, 2)) {
         print "[Bitbucket Server $v](https://confluence.atlassian.com$u)\n";
     }' "$releases"

