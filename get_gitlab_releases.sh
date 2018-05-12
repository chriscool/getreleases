#!/bin/sh

releases="$1"

gitlab_releases="https://about.gitlab.com/blog/categories/releases/"

die () {
	printf "%s\n" "$@"
	exit 1
}

if test -z "$releases"
then
	tmpdir=$(mktemp -d) || die "Failed to create temp dir using mktemp."

	releases="$tmpdir/gitlab_releases.html"

	wget "$gitlab_releases" -O "$releases" ||
		die "Failed to wget '$gitlab_releases'."
fi

perl -n - "$releases" <<'EOF'
@a = m|href=['"](/\d+/\d+/\d+/[^/]+/)['"][^>]*>([^<]+)<|gsm;
#print join("\n", @a);
while (my ($u, $v) = splice (@a, 0, 2)) {
         $v =~ s/\s+$//;
         print "[$v](https://about.gitlab.com$u)\n";
};
EOF
