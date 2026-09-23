// Package dtapps is the skill content for DIME Terminal apps, embedded so a
// consumer gets it as a Go dependency pinned in go.mod rather than as a clone
// resolved at run time.
//
// WHAT IS HERE AND WHAT IS NOT. This repository holds what a venue knows:
// endpoints, parameters, instrument naming, error codes. It does NOT hold the
// credential surface — which hosts a secret may be sent to, which routes, how
// the proxy signs — because that is enforced by DIME Terminal's own code and
// belongs beside it. A pull request here cannot change where anyone's
// credential is allowed to go.
package dtapps

import "embed"

// FS is the apps tree: apps/<id>/skills.yaml plus apps/<id>/skills/<name>/.
//
// `all:` rather than a bare pattern so a skill carrying a dotfile is embedded
// too, matching how DIME Terminal embeds its own manifests.
//
//go:embed all:apps
var FS embed.FS
