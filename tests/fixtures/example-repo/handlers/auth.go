package handlers

import (
	"encoding/json"
	"net/http"
)

type loginRequest struct {
	Email    string `json:"email"`
	Password string `json:"password"`
}

type tokenResponse struct {
	Access  string `json:"access_token"`
	Refresh string `json:"refresh_token"`
}

func HandleLogin(w http.ResponseWriter, r *http.Request) {
	var req loginRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid request", http.StatusBadRequest)
		return
	}
	resp := tokenResponse{
		Access:  "access-token-placeholder",
		Refresh: "refresh-token-placeholder",
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

func HandleRefresh(w http.ResponseWriter, r *http.Request) {
	token := r.Header.Get("X-Refresh-Token")
	if token == "" {
		http.Error(w, "missing refresh token", http.StatusUnauthorized)
		return
	}
	resp := tokenResponse{Access: "new-access-token"}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

func HandleLogout(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusNoContent)
}
