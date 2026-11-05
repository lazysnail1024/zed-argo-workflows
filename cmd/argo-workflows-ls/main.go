package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"github.com/lazysnail1024/k8s-crd-toolkit/source"
	"github.com/lazysnail1024/zed-argo-workflows/compat"
	"github.com/lazysnail1024/zed-argo-workflows/internal/analysis"
	"github.com/lazysnail1024/zed-argo-workflows/internal/buildinfo"
	"github.com/lazysnail1024/zed-argo-workflows/internal/fileuri"
	"github.com/lazysnail1024/zed-argo-workflows/internal/lsp"
	"github.com/lazysnail1024/zed-argo-workflows/internal/workspace"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"
)

func main() { os.Exit(run(os.Args[1:])) }
func emit(v any) {
	if err := json.NewEncoder(os.Stdout).Encode(v); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
}
func run(args []string) int {
	return runContext(context.Background(), args)
}

func runContext(parent context.Context, args []string) int {
	if len(args) == 0 {
		fmt.Fprintln(os.Stderr, "usage: argo-workflows-ls version --json | check | serve --stdio | help")
		return 2
	}
	switch args[0] {
	case "help", "--help", "-h":
		if len(args) != 1 {
			return 2
		}
		fmt.Println("argo-workflows-ls: version --json; check --root <dir> --format json|human; serve --stdio\nTarget 3.6.0 is accepted for the local editing contract; other known targets are experimental.")
		return 0
	case "version":
		if len(args) != 2 || args[1] != "--json" {
			fmt.Fprintln(os.Stderr, "usage: version --json")
			return 2
		}
		emit(map[string]any{"product": "argo-workflows", "serverVersion": buildinfo.ServerVersion, "adapterContractVersion": 1, "analysisReady": true, "defaultTargetVersion": compat.DefaultVersion, "acceptedTargetVersions": []string{compat.DefaultVersion}, "configSchemaVersions": []int{1}, "reportSchemaVersions": []int{1}, "toolkitVersion": "v0.0.0-20261103033718-ae52b82ec6c9", "catalogHash": compat.Hash(), "sourceCommit": buildinfo.Commit(), "dirty": buildinfo.IsDirty(), "buildMode": buildinfo.BuildMode})
		return 0
	case "check":
		flags := flag.NewFlagSet("check", flag.ContinueOnError)
		flags.SetOutput(os.Stderr)
		root := flags.String("root", ".", "workspace root")
		format := flags.String("format", "human", "json or human")
		scope := flags.String("scope", "", "scope")
		file := flags.String("file", "", "output file filter")
		target := flags.String("target-version", "", "exact target")
		warningsAsErrors := flags.Bool("warnings-as-errors", false, "fail on warnings")
		if flags.Parse(args[1:]) != nil || flags.NArg() != 0 {
			return 2
		}
		if *format != "json" && *format != "human" {
			fmt.Fprintln(os.Stderr, "invalid format")
			return 2
		}
		started := time.Now()
		configurationErrors := []string{}
		notEvaluated := []string{}
		diagnostics := []analysis.Diagnostic{}
		filesAnalyzed, resourcesAnalyzed := 0, 0
		targets := []workspace.Target{}
		var filter string
		if *file != "" {
			absolute, err := filepath.Abs(*file)
			if err != nil {
				configurationErrors = append(configurationErrors, err.Error())
			} else {
				filter, err = fileuri.FromPath(absolute)
				if err != nil {
					configurationErrors = append(configurationErrors, err.Error())
				}
			}
		}
		signalContext, stop := signal.NotifyContext(parent, os.Interrupt, syscall.SIGTERM)
		defer stop()
		ctx, cancel := context.WithTimeout(signalContext, 5*time.Second)
		defer cancel()
		ctx = analysis.WithInvocation(ctx)
		input, err := workspace.LoadContext(ctx, *root, *scope, *target)
		if err != nil {
			configurationErrors = append(configurationErrors, err.Error())
		} else {
			for _, t := range input.Targets {
				targets = append(targets, t)
				if t.Experimental {
					notEvaluated = append(notEvaluated, compat.ExperimentalReason(t.Version))
				}
			}
			// An empty input does not turn an explicitly selected experimental
			// target into an accepted check.
			if len(input.Targets) == 0 {
				version := input.Config.TargetVersion
				for _, configured := range input.Config.Scopes {
					if configured.Name == *scope && configured.TargetVersion != "" {
						version = configured.TargetVersion
					}
				}
				if *target != "" {
					version = *target
				}
				if !compat.Accepted(version) {
					notEvaluated = append(notEvaluated, compat.ExperimentalReason(version))
				}
			}
			notEvaluated = append(notEvaluated, input.NotEvaluated...)
			localResults := map[source.URI]analysis.Result{}
			incompleteTargets := map[string]bool{}
			matched := filter == ""
			for first := 0; first < len(input.Documents); {
				if ctx.Err() != nil {
					break
				}
				count := analysis.PreparseBatch(ctx, input.Documents[first:])
				for _, document := range input.Documents[first : first+count] {
					if ctx.Err() != nil {
						break
					}
					result := analysis.AnalyzeSchema(ctx, document.Snapshot, document.Target.Version)
					localResults[document.Snapshot.URI()] = result
					if !result.Completed {
						incompleteTargets[document.Target.ID] = true
					}
					notEvaluated = append(notEvaluated, result.NotEvaluated...)
					if filter == "" || filter == string(document.Snapshot.URI()) {
						for i := range result.Diagnostics {
							result.Diagnostics[i].TargetID = document.Target.ID
						}
						diagnostics = append(diagnostics, result.Diagnostics...)
						matched = true
					}
				}
				first += count
			}
			externalDiagnostics, completion := analysis.ExternalDiagnostics(ctx, input)
			for _, document := range input.Documents {
				if !completion[document.Snapshot.URI()].Completed {
					incompleteTargets[document.Target.ID] = true
					if localResults[document.Snapshot.URI()].Completed {
						notEvaluated = append(notEvaluated, "external analysis incomplete: "+string(document.Snapshot.URI()))
					}
				}
			}
			for i := range targets {
				targets[i].AnalysisComplete = !targets[i].Experimental && !incompleteTargets[targets[i].ID] && !input.IncompleteScopes[targets[i].ID] && len(input.NotEvaluated) == 0 && ctx.Err() == nil
			}
			for uri, local := range localResults {
				done := completion[uri]
				if local.Completed && done.Completed {
					filesAnalyzed++
				}
				for _, span := range local.Resources {
					if done.Resources[span] {
						resourcesAnalyzed++
					}
				}
			}
			for _, d := range externalDiagnostics {
				if filter == "" || filter == d.URI {
					diagnostics = append(diagnostics, d)
				}
			}
			if !matched {
				configurationErrors = append(configurationErrors, "file is not part of the selected scope")
			}
		}
		if ctx.Err() != nil {
			notEvaluated = append(notEvaluated, ctx.Err().Error())
		}
		complete := len(configurationErrors) == 0 && len(notEvaluated) == 0 && ctx.Err() == nil
		if *format == "json" {
			emit(map[string]any{"reportSchemaVersion": 1, "product": "argo-workflows", "toolVersion": buildinfo.ServerVersion, "catalogHash": compat.Hash(), "inputSource": "disk", "validationScope": "local-editing-contract", "analysisComplete": complete, "notEvaluated": notEvaluated, "filesAnalyzed": filesAnalyzed, "resourcesAnalyzed": resourcesAnalyzed, "targets": targets, "diagnostics": diagnostics, "configurationErrors": configurationErrors, "elapsedMilliseconds": time.Since(started).Milliseconds()})
		} else {
			for _, d := range diagnostics {
				fmt.Printf("%s:%d:%d: %s %s\n", d.URI, d.Range.Start.Line+1, d.Range.Start.Character+1, d.Code, d.Message)
			}
			if !complete {
				fmt.Fprintln(os.Stderr, "analysis incomplete:", notEvaluated, configurationErrors)
			}
		}
		if ctx.Err() != nil {
			return 3
		}
		if !complete {
			return 2
		}
		for _, diagnostic := range diagnostics {
			if diagnostic.Severity == "Error" || *warningsAsErrors && diagnostic.Severity == "Warning" {
				return 1
			}
		}
		return 0
	case "serve":
		if len(args) != 2 || args[1] != "--stdio" {
			fmt.Fprintln(os.Stderr, "usage: serve --stdio")
			return 2
		}
		return lsp.Serve(os.Stdin, os.Stdout, os.Stderr)
	case "render-check":
		return renderCheck(args[1:])
	case "lint-official":
		return lintOfficial(args[1:])
	case "explain-config":
		flags := flag.NewFlagSet("explain-config", flag.ContinueOnError)
		flags.SetOutput(os.Stderr)
		root := flags.String("root", ".", "workspace root")
		if flags.Parse(args[1:]) != nil || flags.NArg() != 0 {
			return 2
		}
		input, err := workspace.Load(*root, "", "")
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 2
		}
		emit(map[string]any{"product": "argo-workflows", "schemaVersion": 1, "root": input.Root, "configuration": input.Config, "targets": input.Targets, "analysisReady": true, "notEvaluated": input.NotEvaluated})
		return 0
	default:
		fmt.Fprintf(os.Stderr, "unknown command: %s\n", args[0])
		return 2
	}
}
