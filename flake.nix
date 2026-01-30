{
  description = "BibTeX Validator - Validates BibTeX entries against DBLP";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};

      # Create custom packages for missing dependencies
      python3Packages = pkgs.python3Packages;

      pythonEnv = pkgs.python3.withPackages (ps:
        with ps; [
          bibtexparser
          requests
          scholarly
          pip
          tenacity
          nest-asyncio
          # Custom packages
          # semanticscholar
          # free-proxy
        ]);
    in {
      devShells.${system}.default = pkgs.mkShell {
        buildInputs = [ pythonEnv ];
        shellHook = ''
          # Set up a local Python environment
          export PIP_PREFIX="$(pwd)/_build/pip_packages"
          export PYTHONPATH="$PIP_PREFIX/${pkgs.python3.sitePackages}:$PYTHONPATH"
          export PATH="$PIP_PREFIX/bin:$PATH"

          # Create the directory if it doesn't exist
          mkdir -p "$PIP_PREFIX"

          echo "Python environment ready!"
          echo "You can now run: python3 bib.py bibliography.bib"
          echo ""
          echo "To install missing packages locally, run:"
          echo "  pip install --user semanticscholar==0.8.4 free-proxy==1.1.1"
        '';
      };

      packages.${system}.default = pkgs.writeShellScriptBin "bib-validator" ''
        ${pythonEnv}/bin/python ${./bib.py} "$@"
      '';
    };
}
