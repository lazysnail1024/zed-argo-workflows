#!/usr/bin/env python3
"""Argument validity and actual required-input supply are independent."""
from pathlib import Path
import importlib.util
import sys

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('boundaries', HERE / 'wf-inheritance-family-boundaries.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)
f = b.f
CASES = ['path-without-literal', 'path-literal-control', 'configmap-supply-control']


def scenarios():
    return [{'name': name, 'version': '3.6.0', 'targets': []} for name in CASES]


def row(case, version):
    name = case['name']
    form = '{configMapKeyRef: {name: cfg, key: p}}' if name == 'configmap-supply-control' else '{path: /tmp/value}'
    literal = 'value: literal, ' if name == 'path-literal-control' else ''
    text = 'apiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: caller, namespace: argo}\nspec:\n'
    text += '  entrypoint: [[entry|main]]\n  arguments: {parameters: [{name: [[argument|p]], ' + literal + 'valueFrom: [[form|' + form + ']]}]}\n'
    text += '  templates:\n    - name: main\n      inputs: {parameters: [{name: [[inputDecl|p]]}]}\n      container: {image: alpine, args: ["{{inputs.parameters.[[inputUse|p]]}}"]}\n'
    text += '---\napiVersion: argoproj.io/v1alpha1\nkind: Workflow\nmetadata: {name: sibling, namespace: argo}\nspec:\n  entrypoint: main\n  arguments: {parameters: [{name: [[siblingDecl|identity]], value: stable}]}\n  templates:\n    - name: main\n      container: {image: alpine, args: ["\U0001f40c {{workflow.parameters.[[siblingUse|identity]]}}"]}\n'
    errors = [] if name == 'configmap-supply-control' else [f.error('form', 'WF_ARGUMENT_VALUE_INVALID', 'argument parameter p has a valueFrom form that is not allowed')]
    if name == 'path-without-literal':
        errors.append(f.missing('entry', 'p'))
    return name, text.replace('\n', '\r\n'), errors


def query_tags(case, version):
    return [(tag, [decl], uses, uses + [decl]) for tag, decl, uses in [
        ('siblingUse', 'siblingDecl', ['siblingUse']),
        ('siblingDecl', 'siblingDecl', ['siblingUse']),
        ('inputUse', 'inputDecl', ['argument', 'inputUse']),
    ]], []


b.scenarios, b.row, b.query_tags = scenarios, row, query_tags
b.__file__ = __file__
if __name__ == '__main__':
    raise SystemExit(b.run())
