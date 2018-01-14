#!/bin/sh

gitlab_releases="https://about.gitlab.com/blog/categories/release/"

die () {
	printf "%s\n" "$@"
	exit 1
}

tmpdir=$(mktemp -d) || die "Failed to create temp dir using mktemp."

wget "$gitlab_releases" -O "$tmpdir/gitlab_releases.html" ||
	die "Failed to wget '$gitlab_releases'."

perl -ne '@a = m/href="(\/\d+\/\d+\/\d+\/gitlab-[\d-]+-released\/)">([^<]+)</gsm;
     while (my ($u, $v) = splice (@a, 0, 2)) {
         $v =~ s/\s+$//;
         print "[$v](https://about.gitlab.com/$u)\n";
     }' "$tmpdir/gitlab_releases.html"
