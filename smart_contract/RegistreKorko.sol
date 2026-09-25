// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title Registre public du parc KORKO
/// @notice Chaque planche est un objet unique, rattaché à sa station
///         d'origine. Chaque départ et chaque retour d'une planche du parc,
///         observé par une de ses stations, est inscrit ici et reste
///         vérifiable par tous.
/// @dev Un départ ou un retour qui contredit l'état de la planche (déjà en
///      mer, jamais partie) n'est pas rejeté : il reste visible. Seules une
///      planche ou une station inconnues sont refusées.
contract RegistreKorko {
    struct Station {
        bool existe;
        uint32 nombrePlanches;
    }

    struct Planche {
        bool existe;
        bool enMer;
        uint32 nombreDeparts;
        string stationOrigine;
        string derniereStation;
    }

    address public immutable proprietaire;

    mapping(string => Station) private _stations;
    mapping(uint16 => Planche) private _planches;
    string[] private _codesStations;
    uint16[] private _numerosPlanches;

    event StationAjoutee(string station);
    event StationRetiree(string station);
    event PlancheAjoutee(uint16 indexed numero, string station);
    event PlancheRetiree(uint16 indexed numero);

    /// @notice Une station a vu partir la planche.
    /// @param instantMs horloge de la station, en millisecondes : date Unix
    ///        sur une vraie station, temps de simulation en démo. La date
    ///        réelle de l'inscription est celle du bloc.
    event Depart(uint16 indexed numero, string station, uint64 instantMs);

    /// @notice Une station a vu revenir la planche, chez elle ou ailleurs.
    /// @param instantMs horloge de la station, en millisecondes (voir Depart)
    event Retour(uint16 indexed numero, string station, uint64 instantMs);

    error NonAutorise(address appelant);
    error CodeVide();
    error StationInconnue(string station);
    error StationExistante(string station);
    error StationNonVide(string station, uint32 nombrePlanches);
    error StationOccupee(string station, uint16 numero);
    error PlancheInconnue(uint16 numero);
    error PlancheExistante(uint16 numero);

    modifier seulementProprietaire() {
        if (msg.sender != proprietaire) revert NonAutorise(msg.sender);
        _;
    }

    modifier stationConnue(string calldata code) {
        if (!_stations[code].existe) revert StationInconnue(code);
        _;
    }

    modifier plancheConnue(uint16 numero) {
        if (!_planches[numero].existe) revert PlancheInconnue(numero);
        _;
    }

    constructor() {
        proprietaire = msg.sender;
    }

    /// @notice Ouvre une nouvelle station.
    function ajouterStation(string calldata code)
        external
        seulementProprietaire
    {
        if (bytes(code).length == 0) revert CodeVide();
        if (_stations[code].existe) revert StationExistante(code);
        _stations[code].existe = true;
        _codesStations.push(code);
        emit StationAjoutee(code);
    }

    /// @notice Ferme une station. Ses planches doivent être retirées avant,
    ///         et aucune planche d'une autre station ne doit y être rangée.
    function retirerStation(string calldata code)
        external
        seulementProprietaire
        stationConnue(code)
    {
        uint32 restantes = _stations[code].nombrePlanches;
        if (restantes != 0) revert StationNonVide(code, restantes);
        _verifierAucuneRangee(code);
        delete _stations[code];
        _retirerCode(code);
        emit StationRetiree(code);
    }

    /// @notice Inscrit une planche, identifiée par le numéro de sa balise.
    function ajouterPlanche(uint16 numero, string calldata station)
        external
        seulementProprietaire
        stationConnue(station)
    {
        if (_planches[numero].existe) revert PlancheExistante(numero);
        Planche storage planche = _planches[numero];
        planche.existe = true;
        planche.stationOrigine = station;
        planche.derniereStation = station;
        _stations[station].nombrePlanches += 1;
        _numerosPlanches.push(numero);
        emit PlancheAjoutee(numero, station);
    }

    /// @notice Retire une planche du parc, même en mer (perdue, volée).
    ///         Son historique reste lisible dans les événements passés.
    function retirerPlanche(uint16 numero)
        external
        seulementProprietaire
        plancheConnue(numero)
    {
        _stations[_planches[numero].stationOrigine].nombrePlanches -= 1;
        delete _planches[numero];
        _retirerNumero(numero);
        emit PlancheRetiree(numero);
    }

    /// @notice Inscrit le départ d'une planche vu par une station.
    /// @param instantMs horloge de la station, en millisecondes (voir Depart)
    function enregistrerDepart(
        uint16 numero,
        string calldata station,
        uint64 instantMs
    )
        external
        seulementProprietaire
        plancheConnue(numero)
        stationConnue(station)
    {
        Planche storage planche = _planches[numero];
        planche.enMer = true;
        planche.nombreDeparts += 1;
        planche.derniereStation = station;
        emit Depart(numero, station, instantMs);
    }

    /// @notice Inscrit le retour d'une planche vu par une station.
    /// @param instantMs horloge de la station, en millisecondes (voir Depart)
    function enregistrerRetour(
        uint16 numero,
        string calldata station,
        uint64 instantMs
    )
        external
        seulementProprietaire
        plancheConnue(numero)
        stationConnue(station)
    {
        Planche storage planche = _planches[numero];
        planche.enMer = false;
        planche.derniereStation = station;
        emit Retour(numero, station, instantMs);
    }

    /// @notice L'état d'une station : existe, nombre de planches d'origine.
    function lireStation(string calldata code)
        external
        view
        returns (Station memory)
    {
        return _stations[code];
    }

    /// @notice L'état d'une planche : en mer ou non, départs, stations.
    function lirePlanche(uint16 numero)
        external
        view
        returns (Planche memory)
    {
        return _planches[numero];
    }

    /// @notice Les codes de toutes les stations ouvertes.
    function listerStations() external view returns (string[] memory) {
        return _codesStations;
    }

    /// @notice Les numéros de toutes les planches du parc.
    function listerPlanches() external view returns (uint16[] memory) {
        return _numerosPlanches;
    }

    /// @dev Le parc compte quelques dizaines d'éléments : un parcours
    ///      linéaire reste lisible et bon marché.
    function _retirerCode(string calldata code) private {
        bytes32 cible = keccak256(bytes(code));
        uint256 dernier = _codesStations.length - 1;
        for (uint256 i = 0; i <= dernier; i++) {
            if (keccak256(bytes(_codesStations[i])) == cible) {
                _codesStations[i] = _codesStations[dernier];
                _codesStations.pop();
                return;
            }
        }
    }

    /// @dev Même parcours linéaire que _retirerCode.
    function _retirerNumero(uint16 numero) private {
        uint256 dernier = _numerosPlanches.length - 1;
        for (uint256 i = 0; i <= dernier; i++) {
            if (_numerosPlanches[i] == numero) {
                _numerosPlanches[i] = _numerosPlanches[dernier];
                _numerosPlanches.pop();
                return;
            }
        }
    }

    /// @dev Refuse de fermer une station où une planche d'une autre station
    ///      est encore rangée.
    function _verifierAucuneRangee(string calldata code) private view {
        bytes32 cible = keccak256(bytes(code));
        for (uint256 i = 0; i < _numerosPlanches.length; i++) {
            uint16 numero = _numerosPlanches[i];
            Planche storage planche = _planches[numero];
            bool rangeeIci = !planche.enMer
                && keccak256(bytes(planche.derniereStation)) == cible;
            if (rangeeIci) revert StationOccupee(code, numero);
        }
    }
}
