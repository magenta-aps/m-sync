# SPDX-FileCopyrightText: 2026 Magenta ApS <info@magenta.dk>
# SPDX-License-Identifier: MPL-2.0
{
  description = "mSync development environment";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = {nixpkgs, ...}: let
    forAllSystems = nixpkgs.lib.genAttrs nixpkgs.lib.systems.flakeExposed;
  in {
    # `nix fmt`
    formatter = forAllSystems (system: nixpkgs.legacyPackages.${system}.alejandra);

    # `nix develop`
    devShells = forAllSystems (
      system: let
        pkgs = nixpkgs.legacyPackages.${system};
      in {
        default = pkgs.mkShell {
          packages = [
            pkgs.python312
            pkgs.poetry
          ];
          env = {
            # pip wheels (e.g. greenlet) link against libstdc++, which is not
            # in the default dynamic loader path on NixOS
            LD_LIBRARY_PATH = "${pkgs.stdenv.cc.cc.lib}/lib";
            # keep the poetry venv in-project so basedpyright resolves
            # imports through venvPath/venv in pyproject.toml
            POETRY_VIRTUALENVS_IN_PROJECT = "true";
          };
          shellHook = ''
            poetry install --no-root
            eval $(poetry env activate)
          '';
        };
      }
    );
  };
}
